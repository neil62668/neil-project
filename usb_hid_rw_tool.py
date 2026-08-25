import sys
import os
import time
import ctypes
import ctypes.wintypes as wt
from datetime import datetime
from pathlib import Path
import hid

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QLabel,
    QMessageBox,
    QFileDialog,
    QCheckBox,
)
from PyQt6.QtCore import QCoreApplication, QThread, pyqtSignal
from PyQt6.QtGui import QIcon


# ---------------------------------------------------------------------------
# 資源路徑解析函數
# ---------------------------------------------------------------------------
def get_resource_path(relative_path):
    """取得資源檔案的絕對路徑，兼容開發環境與 PyInstaller 打包環境"""
    if hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path


# ---------------------------------------------------------------------------
# Windows API 定義：讀取 CAPS 與執行 HidD_GetInputReport
# ---------------------------------------------------------------------------
class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wt.USHORT),
        ("UsagePage", wt.USHORT),
        ("InputReportByteLength", wt.USHORT),
        ("OutputReportByteLength", wt.USHORT),
        ("FeatureReportByteLength", wt.USHORT),
        ("Reserved", wt.USHORT * 17),
        ("NumberLinkCollectionNodes", wt.USHORT),
        ("NumberInputButtonCaps", wt.USHORT),
        ("NumberInputValueCaps", wt.USHORT),
        ("NumberInputDataIndices", wt.USHORT),
        ("NumberOutputButtonCaps", wt.USHORT),
        ("NumberOutputValueCaps", wt.USHORT),
        ("NumberFeatureButtonCaps", wt.USHORT),
        ("NumberFeatureValueCaps", wt.USHORT),
        ("NumberFeatureDataIndices", wt.USHORT),
    ]


def get_hid_report_lengths(device_path):
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3

    handle = ctypes.windll.kernel32.CreateFileW(
        device_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )

    if handle == -1 or handle == 0xFFFFFFFF:
        handle = ctypes.windll.kernel32.CreateFileW(
            device_path,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )

    if handle == -1 or handle == 0xFFFFFFFF:
        return 0, 0, 0

    preparsed_data = ctypes.c_void_p()
    caps = HIDP_CAPS()
    i_len, o_len, f_len = 0, 0, 0

    try:
        if ctypes.windll.hid.HidD_GetPreparsedData(
            handle, ctypes.byref(preparsed_data)
        ):
            if (
                ctypes.windll.hid.HidP_GetCaps(preparsed_data, ctypes.byref(caps))
                == 0x00110000
            ):
                i_len = caps.InputReportByteLength
                o_len = caps.OutputReportByteLength
                f_len = caps.FeatureReportByteLength
            ctypes.windll.hid.HidD_FreePreparsedData(preparsed_data)
    except Exception:
        pass
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)

    return i_len, o_len, f_len


def win32_get_input_report(device_path, request_bytes, expected_i_len):
    """
    透過 Win32 HidD_GetInputReport 主動發送 Control Pipe Request 索取 Input Report
    """
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3

    path_str = (
        device_path.decode("utf-8", errors="ignore")
        if isinstance(device_path, bytes)
        else str(device_path)
    )

    handle = ctypes.windll.kernel32.CreateFileW(
        path_str,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )

    if handle == -1 or handle == 0xFFFFFFFF:
        return False, "CreateFileW 開啟失敗 (Handle Error)"

    try:
        buf_len = max(len(request_bytes), expected_i_len)
        buffer = ctypes.create_string_buffer(buf_len)

        # 將 request 內容拷貝至 buffer 前段
        ctypes.memmove(buffer, request_bytes, len(request_bytes))

        # 呼叫 HidD_GetInputReport
        success = ctypes.windll.hid.HidD_GetInputReport(handle, buffer, buf_len)

        if success:
            res_bytes = bytes(buffer.raw[:expected_i_len])
            return True, res_bytes
        else:
            err_code = ctypes.windll.kernel32.GetLastError()
            return False, f"HidD_GetInputReport 失敗 (Win32 Error Code: {err_code})"

    except Exception as e:
        return False, f"Win32 API 調用例外: {str(e)}"
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# 全時背景監測 Thread
# ---------------------------------------------------------------------------
class USBGlobalMonitorThread(QThread):
    """Monitor global USB HID device changes in a background thread."""

    usb_changed = pyqtSignal()
    target_disconnected = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.running = True
        self.target_path = None
        self.last_device_paths = set()

    def set_target_path(self, path):
        self.target_path = path

    def clear_target_path(self):
        self.target_path = None

    def run(self):
        initial_devs = hid.enumerate()
        self.last_device_paths = {d.get("path") for d in initial_devs}

        while self.running:
            time.sleep(0.5)
            if not self.running:
                break

            current_devs = hid.enumerate()
            current_paths = {d.get("path") for d in current_devs}

            if current_paths != self.last_device_paths:
                self.last_device_paths = current_paths
                self.usb_changed.emit()

            if self.target_path:
                if self.target_path not in current_paths:
                    self.target_disconnected.emit()
                    self.target_path = None

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# GUI 主程式
# ---------------------------------------------------------------------------
class USBHIDApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dev = None
        self.connected_path = None
        self.monitor_thread = None
        self.device_info_list = []
        self.cmd_list = []
        self.stop_requested = False

        # 動態 Report 長度紀錄 (由 HIDP_CAPS 決定)
        self.current_i_len = 64
        self.current_o_len = 64
        self.current_f_len = 64

        self.initUI()
        self.start_global_monitor()

    def initUI(self):
        self.setWindowTitle("USB HID Report 工具")
        self.resize(780, 600)

        main_layout = QVBoxLayout()

        # 1. 裝置選擇區域
        dev_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_devices)

        self.device_combo = QComboBox()

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.toggle_connect)

        dev_layout.addWidget(self.refresh_btn)
        dev_layout.addWidget(self.device_combo, stretch=1)
        dev_layout.addWidget(self.connect_btn)
        main_layout.addLayout(dev_layout)

        # 2. CMD 清單載入與自動執行區域
        cmd_layout = QHBoxLayout()
        self.load_cmd_btn = QPushButton("Load CMD List")
        self.load_cmd_btn.clicked.connect(self.load_cmd_file)

        self.cmd_combo = QComboBox()
        self.cmd_combo.addItem("-- 請先載入命令清單 --")
        self.cmd_combo.currentIndexChanged.connect(self.on_cmd_selected)
        self.cmd_combo.setEnabled(False)

        delay_label = QLabel("Delay (ms):")
        self.delay_input = QLineEdit("100")
        self.delay_input.setFixedWidth(42)

        self.run_one_btn = QPushButton("Run One")
        self.run_one_btn.clicked.connect(self.run_one)
        self.run_one_btn.setFixedWidth(66)
        self.run_one_btn.setEnabled(False)

        self.run_all_btn = QPushButton("Run All")
        self.run_all_btn.clicked.connect(self.run_all)
        self.run_all_btn.setFixedWidth(66)
        self.run_all_btn.setEnabled(False)

        self.stop_run_btn = QPushButton("Stop")
        self.stop_run_btn.clicked.connect(self.stop_run_all)
        self.stop_run_btn.setFixedWidth(66)
        self.stop_run_btn.setEnabled(False)

        cmd_layout.addWidget(self.load_cmd_btn)
        cmd_layout.addWidget(self.cmd_combo, stretch=1)
        cmd_layout.addWidget(delay_label)
        cmd_layout.addWidget(self.delay_input)
        cmd_layout.addWidget(self.run_one_btn)
        cmd_layout.addWidget(self.run_all_btn)
        cmd_layout.addWidget(self.stop_run_btn)
        main_layout.addLayout(cmd_layout)

        # 3. HEX 資料輸入與 Report ID 轉置設定區域
        data_layout = QVBoxLayout()
        self.input_label = QLabel("HEX 資料輸入 (自動補零或裁切至裝置預設封包長度)")
        data_layout.addWidget(self.input_label)

        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("例如: 06 06 00 05 5A 02 00 23 2F")
        data_layout.addWidget(self.hex_input)

        # 新增：Report ID 自動轉換控制列
        convert_layout = QHBoxLayout()
        self.chk_auto_convert = QCheckBox("啟用 Get Report 封包自動轉換 Report ID:")
        self.chk_auto_convert.setChecked(True)  # 預設啟用
        self.chk_auto_convert.toggled.connect(self.on_convert_toggled)

        convert_label = QLabel("0x")
        self.get_report_id_input = QLineEdit("07")
        self.get_report_id_input.setFixedWidth(28)
        self.get_report_id_input.setMaxLength(2)

        convert_layout.addWidget(self.chk_auto_convert)
        convert_layout.addWidget(convert_label)
        convert_layout.addWidget(self.get_report_id_input)
        convert_layout.addStretch()
        data_layout.addLayout(convert_layout)

        main_layout.addLayout(data_layout)

        # 4. 功能按鈕區域
        btn_layout = QHBoxLayout()
        self.set_report_btn = QPushButton("Set Report")
        self.set_report_btn.clicked.connect(self.set_report)
        self.set_report_btn.setEnabled(False)

        self.get_report_btn = QPushButton("Get Report (Control Pipe)")
        self.get_report_btn.clicked.connect(self.get_report)
        self.get_report_btn.setEnabled(False)

        btn_layout.addWidget(self.set_report_btn)
        btn_layout.addWidget(self.get_report_btn)
        main_layout.addLayout(btn_layout)

        # 5. 訊息與紀錄顯示區域
        log_header_layout = QHBoxLayout()
        log_header_layout.addWidget(QLabel("通訊日誌 (Log):"))
        log_header_layout.addStretch()

        self.clear_log_btn = QPushButton("Clear Log")
        self.clear_log_btn.clicked.connect(self.clear_log)
        self.save_log_btn = QPushButton("Save Log")
        self.save_log_btn.clicked.connect(self.save_log)

        log_header_layout.addWidget(self.clear_log_btn)
        log_header_layout.addWidget(self.save_log_btn)
        main_layout.addLayout(log_header_layout)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        main_layout.addWidget(self.log_text)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self.refresh_devices()

    # ---------------------------------------------------------------------------
    # UI 事件處理 (Report ID 自動轉置開關)
    # ---------------------------------------------------------------------------
    def on_convert_toggled(self, checked):
        """根據「自動Report ID轉換」勾選狀態連動決定輸入框是否可用"""
        self.get_report_id_input.setEnabled(checked)

    # ---------------------------------------------------------------------------
    # 全時背景監控管理
    # ---------------------------------------------------------------------------
    def start_global_monitor(self):
        self.monitor_thread = USBGlobalMonitorThread()
        self.monitor_thread.usb_changed.connect(self.on_usb_changed)
        self.monitor_thread.target_disconnected.connect(
            self.handle_unexpected_disconnect
        )
        self.monitor_thread.start()

    def on_usb_changed(self):
        if self.dev is None:
            self.log("[系統] 偵測到 USB 裝置變更，自動更新選單...")
            self.refresh_devices()

    def handle_unexpected_disconnect(self):
        if self.dev is None:
            return

        self.stop_requested = True
        if self.monitor_thread:
            self.monitor_thread.clear_target_path()

        try:
            if self.dev:
                self.dev.close()
        except Exception:
            pass

        self.dev = None
        self.connected_path = None

        self.connect_btn.setText("Connect")
        self.device_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.set_report_btn.setEnabled(False)
        self.get_report_btn.setEnabled(False)
        self.stop_run_btn.setEnabled(False)
        self.input_label.setText("HEX 資料輸入 (自動補零或裁切至裝置預設封包長度)")
        self.update_run_all_state()

        self.log("[系統] 警告: 當前連線的 USB 裝置已被拔除，已自動中斷連線！")

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setWindowTitle("裝置拔除提示")
        msg_box.setText("偵測到 USB 裝置已被拔除，系統已自動斷開連線！")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.exec()

        self.refresh_devices()

    def log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S.%f]")[:-3] + "]"
        if "\n" in message:
            lines = message.split("\n")
            formatted_message = f"{timestamp} {lines[0]}"
            for line in lines[1:]:
                formatted_message += f"\n{timestamp} {line}"
            self.log_text.append(formatted_message)
        else:
            self.log_text.append(f"{timestamp} {message}")

    def clear_log(self):
        self.log_text.clear()

    def save_log(self):
        log_content = self.log_text.toPlainText()
        if not log_content.strip():
            QMessageBox.information(self, "提示", "目前沒有任何 Log 紀錄可供儲存！")
            return

        default_filename = f"hid_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "儲存 Log 紀錄",
            default_filename,
            "Text Files (*.txt);;Log Files (*.log);;All Files (*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(log_content)
            self.log(f"[系統] Log 已成功匯出至: {file_path}")
            QMessageBox.information(self, "成功", "Log 紀錄已成功儲存！")
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"儲存檔案失敗:\n{e}")

    def update_run_all_state(self):
        is_connected = self.dev is not None
        has_commands = len(self.cmd_list) > 0

        # 當有連線時，不論有無載入檔案，Run One 均可點擊（以當前輸入框 HEX 為準）
        self.run_one_btn.setEnabled(is_connected)
        self.run_all_btn.setEnabled(is_connected and has_commands)

    def load_cmd_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "開啟命令清單檔案", "", "Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return

        try:
            self.cmd_list.clear()
            self.cmd_combo.clear()
            self.cmd_combo.addItem("-- 請選擇命令 --")

            count = 0
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or not line.startswith("["):
                        continue

                    parts = line.split("],[" if "],[" in line else "],[")
                    if len(parts) == 2:
                        cmd_name = parts[0].lstrip("[").strip()
                        hex_str = parts[1].rstrip("]").strip()

                        self.cmd_list.append((cmd_name, hex_str))

                        display_name = f"#{count+1} {cmd_name}"
                        self.cmd_combo.addItem(display_name)
                        count += 1

            self.log(f"[命令載入] 成功載入 {count} 個命令。")

            # 防呆邏輯：當成功解析到至少一條命令時才啟用 ComboBox，否則保持禁用
            if count > 0:
                self.cmd_combo.setEnabled(True)
            else:
                self.cmd_combo.setEnabled(False)
                QMessageBox.warning(
                    self, "警告", "檔案內未解析到符合 [Name],[HEX] 格式的命令！"
                )

            self.update_run_all_state()

        except Exception as e:
            self.cmd_combo.setEnabled(False)  # 讀檔發生例外時維持禁用
            self.log(f"[錯誤] 載入命令清單檔案失敗: {str(e)}")
            QMessageBox.critical(self, "錯誤", f"讀取檔案失敗:\n{e}")

    def on_cmd_selected(self, index):
        cmd_index = index - 1
        if 0 <= cmd_index < len(self.cmd_list):
            _, hex_str = self.cmd_list[cmd_index]
            self.hex_input.setText(hex_str)

    def stop_run_all(self):
        if not self.stop_requested:
            self.stop_requested = True
            self.stop_run_btn.setEnabled(False)
            self.log("[Run All] 收到使用者中斷請求，等待當前命令完成後將自動停止...")

    def _safe_delay(self, delay_sec):
        start_time = time.time()
        while (time.time() - start_time) < delay_sec:
            QCoreApplication.processEvents()
            time.sleep(0.005)

    def run_one(self):
        if not self.dev:
            return

        current_hex = self.hex_input.text().strip()
        if not current_hex:
            QMessageBox.warning(self, "警告", "HEX 資料輸入欄位不可為空！")
            return

        try:
            delay_ms = float(self.delay_input.text().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            QMessageBox.warning(
                self, "輸入錯誤", "請輸入有效的延遲時間 (正數數字毫秒)！"
            )
            return

        self.log("==========================================")
        self.log(f"[Run One] 開始執行單組命令 (delay={delay_ms:.0f}ms)...")
        self.log("==========================================")

        self.run_one_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)
        self.set_report_btn.setEnabled(False)
        self.get_report_btn.setEnabled(False)

        # 1. 發送 Set Report
        self.set_report()
        QCoreApplication.processEvents()

        if delay_sec > 0:
            self._safe_delay(delay_sec)

        # 2. 接收 Get Report
        self.get_report()
        QCoreApplication.processEvents()

        if delay_sec > 0:
            self._safe_delay(delay_sec)

        self.log("==========================================")
        self.log("[Run One] 單組命令測試完成！")
        self.log("==========================================")

        self.set_report_btn.setEnabled(True)
        self.get_report_btn.setEnabled(True)
        self.update_run_all_state()

    def run_all(self):
        if not self.dev or not self.cmd_list:
            return

        try:
            delay_ms = float(self.delay_input.text().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            QMessageBox.warning(
                self, "輸入錯誤", "請輸入有效的延遲時間 (正整數毫秒單位)！"
            )
            return

        self.stop_requested = False
        self.log("==========================================")
        self.log(
            f"[Run All] 開始執行批次測試，共 {len(self.cmd_list)} 項命令 (間隔 delay={delay_ms:.0f}ms)..."
        )
        self.log("==========================================")

        self.run_one_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)
        self.stop_run_btn.setEnabled(True)
        self.set_report_btn.setEnabled(False)
        self.get_report_btn.setEnabled(False)

        total = len(self.cmd_list)
        executed_count = 0

        for idx, (name, hex_str) in enumerate(self.cmd_list, 1):
            if not self.dev:
                break

            self.log(f">>> [{idx}/{total}] 執行命令: {name} (delay={delay_ms:.0f}ms)")

            self.cmd_combo.setCurrentIndex(idx)
            self.hex_input.setText(hex_str)
            QCoreApplication.processEvents()

            # 1. 發送 Set Report
            self.set_report()
            QCoreApplication.processEvents()

            if delay_sec > 0:
                self._safe_delay(delay_sec)

            # 2. 接收 Get Report
            self.get_report()
            QCoreApplication.processEvents()

            executed_count += 1

            if delay_sec > 0:
                self._safe_delay(delay_sec)

            if self.stop_requested or not self.dev:
                self.log("------------------------------------------")
                self.log(f"[Run All] 已依照請求完成第 {idx} 項命令後安全停止！")
                break

        self.log("==========================================")
        if self.stop_requested:
            self.log(
                f"[Run All] 批次命令已手動中斷！(共完成 {executed_count}/{total} 項命令)"
            )
        else:
            self.log(f"[Run All] 批次命令測試完成！(共執行 {total} 項命令)")
        self.log("==========================================")

        self.stop_requested = False
        self.stop_run_btn.setEnabled(False)
        self.set_report_btn.setEnabled(True)
        self.get_report_btn.setEnabled(True)
        self.update_run_all_state()

    def refresh_devices(self):
        self.device_combo.clear()
        raw_list = hid.enumerate()

        if not raw_list:
            self.device_combo.addItem("未找到任何 HID 裝置")
            self.log("[系統] 未偵測到任何 USB HID 裝置。")
            return

        self.device_info_list = []
        for dev in raw_list:
            prod = dev.get("product_string")
            mfg = dev.get("manufacturer_string")
            if not prod and not mfg:
                continue
            self.device_info_list.append(dev)

        if not self.device_info_list:
            self.device_combo.addItem("未找到具名的 HID 裝置 (已過濾 Unknown)")
            self.log("[系統] 掃描完成，但所有裝置皆為 Unknown 並已自動過濾。")
            return

        self.device_info_list.sort(
            key=lambda d: (
                d.get("vendor_id", 0),
                d.get("product_id", 0),
                (d.get("product_string") or "").lower(),
            )
        )

        for dev in self.device_info_list:
            vid = f"{dev['vendor_id']:04X}"
            pid = f"{dev['product_id']:04X}"
            mfg = dev.get("manufacturer_string") or "Unknown"
            prod = dev.get("product_string") or "Unknown"

            path = dev.get("path")
            path_str = (
                path.decode("utf-8", errors="ignore")
                if isinstance(path, bytes)
                else str(path)
            )

            i_len, o_len, f_len = get_hid_report_lengths(path_str)
            display_str = (
                f"[{vid}, {pid}] (I: {i_len}, O: {o_len}, F: {f_len}) | {prod} ({mfg})"
            )
            self.device_combo.addItem(display_str)

        masked_count = len(raw_list) - len(self.device_info_list)
        self.log(
            f"[系統] 掃描完成：共 {len(self.device_info_list)} 個有效 HID 裝置 (已排序，已自動遮罩 {masked_count} 個 Unknown 裝置)。"
        )

    def toggle_connect(self):
        if self.dev is None:
            idx = self.device_combo.currentIndex()
            if idx < 0 or not self.device_info_list:
                QMessageBox.warning(self, "警告", "請先選擇有效的 USB 裝置！")
                return

            target_dev = self.device_info_list[idx]
            try:
                path = target_dev["path"]
                path_str = (
                    path.decode("utf-8", errors="ignore")
                    if isinstance(path, bytes)
                    else str(path)
                )

                # 開啟裝置前，先動態取得該裝置精確的 Report 長度
                i_len, o_len, f_len = get_hid_report_lengths(path_str)
                self.current_i_len = i_len
                self.current_o_len = o_len
                self.current_f_len = f_len

                self.dev = hid.device()
                self.dev.open_path(path)
                self.dev.set_nonblocking(True)
                self.connected_path = path

                if self.monitor_thread:
                    self.monitor_thread.set_target_path(self.connected_path)

                self.log(
                    f"[連線] 成功連接至: VID={target_dev['vendor_id']:04X}&PID={target_dev['product_id']:04X}"
                )
                self.log(
                    f"[連線] 裝置預設封包長度 -> Input (I): {i_len}, Output (O): {o_len}"
                )

                # 更新介面動態提示長度資訊
                self.input_label.setText(
                    f"HEX 資料輸入 Get Report 預設封包長度: {i_len} Bytes / Set Report 預設封包長度: {o_len} Bytes"
                )

                self.connect_btn.setText("Disconnect")
                self.device_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)
                self.set_report_btn.setEnabled(True)
                self.get_report_btn.setEnabled(True)

            except Exception as e:
                self.log(f"[錯誤] 連接失敗: {str(e)}")
                QMessageBox.critical(self, "錯誤", f"無法連接至該裝置:\n{e}")
                self.dev = None
                self.connected_path = None
        else:
            if self.monitor_thread:
                self.monitor_thread.clear_target_path()

            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
            self.connected_path = None
            self.log("[連線] 已中斷裝置連接。")
            self.connect_btn.setText("Connect")
            self.device_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self.set_report_btn.setEnabled(False)
            self.get_report_btn.setEnabled(False)
            self.input_label.setText("HEX 資料輸入 (自動補零或裁切至裝置預設封包長度)")

        self.update_run_all_state()

    def _prepare_payload(self, target_len, override_report_id=None):
        """
        解析與打包 HEX 字串，並自動對齊至 target_len：
        1. 解析輸入框的 HEX 字串（例如: 06 06 00 05 5A 02 00 23 2F）
        2. 若指定 override_report_id (例如使用者傳入 0x07 或 0x08)，則自動將 Byte 0 覆蓋，
           保持後續 CMD_Length、Chip_Target 與 CMD 本體 (Byte 3 之後) 與原始輸入完全相同。
        3. 長度大於 target_len 則自動裁切，不足則在尾端自動補滿 0x00 至指定長度 (CAPS 數值)。
        """
        raw_text = self.hex_input.text().strip().replace(" ", "")

        if not raw_text:
            raise ValueError("請輸入 HEX 字串！")

        try:
            parsed_bytes = bytearray(bytes.fromhex(raw_text))
        except ValueError:
            raise ValueError("請輸入有效的 HEX 字串（例如：06 06 00 05 5A ...）！")

        # 若指定了動態 Report ID 轉置，則強制覆蓋 Byte 0
        if override_report_id is not None and len(parsed_bytes) > 0:
            parsed_bytes[0] = override_report_id

        # 若輸入長度超過目標 CAPS 長度則自動裁切
        if len(parsed_bytes) > target_len:
            self.log(
                f"[提示] 輸入長度 ({len(parsed_bytes)} Bytes) 大於對應長度 ({target_len} Bytes)，自動進行裁切。"
            )
            return bytes(parsed_bytes[:target_len])

        # 長度不足則向後自動補 0x00 填滿至 CAPS 長度
        padded_bytes = bytes(parsed_bytes).ljust(target_len, b"\x00")
        return padded_bytes

    def set_report(self):
        """依據 OutputReportByteLength (current_o_len) 發送 Set Report 資料"""
        if not self.dev:
            return

        if self.current_o_len == 0:
            QMessageBox.warning(
                self, "不支援", "此 USB HID 裝置未宣告 Output Report (O: 0)！"
            )
            self.log("[錯誤] 無法執行 Set Report: 該裝置 OutputReportByteLength 為 0。")
            return

        try:
            payload = self._prepare_payload(self.current_o_len)
            report_id = payload[0]

            bytes_written = self.dev.write(payload)
            if bytes_written > 0:
                self.log(
                    f"[TX] Set Report 成功 (Report ID: 0x{report_id:02X}, 長度: {bytes_written}/{self.current_o_len} Bytes):\n  -> {payload.hex(' ').upper()}"
                )
            else:
                self.log("[錯誤] Set Report 傳送失敗。")

        except Exception as e:
            self.log(f"[錯誤] Set Report 操作失敗: {str(e)}")
            if "write" in str(e).lower() or "device" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "錯誤", str(e))

    def get_report(self):
        """
        主動透過 Win32 API HidD_GetInputReport 發送 Control Transfer 索取 Input Report。
        邏輯：
        1. 檢查「自動Report ID轉換」功能是否啟用。
        2. 若啟用：解析 Get Report ID 輸入框數值（如: 07 或 08），並將 HEX 命令首位替換為該 ID。
        3. 若停用：不修改首位 Byte，直接依輸入框內容發送。
        4. 自動向後補齊零至 current_i_len (InputReportByteLength)，組成 Request 封包。
        5. 發送至 Control Pipe 索取 Response。
        """
        if not self.connected_path:
            return

        if self.current_i_len == 0:
            QMessageBox.warning(
                self, "不支援", "此 USB HID 裝置未宣告 Input Report (I: 0)！"
            )
            self.log("[錯誤] 無法執行 Get Report: 該裝置 InputReportByteLength 為 0。")
            return

        try:
            target_report_id = None

            # 判斷有無啟用「自動 Report ID 轉換」功能
            if self.chk_auto_convert.isChecked():
                id_hex_str = self.get_report_id_input.text().strip()
                if not id_hex_str:
                    QMessageBox.warning(
                        self,
                        "輸入錯誤",
                        "「自動 Report ID 轉換」已啟用，請在 Report ID 輸入框填寫數值（不可留空）！",
                    )
                    return
                try:
                    target_report_id = int(id_hex_str, 16)
                except ValueError:
                    QMessageBox.warning(
                        self,
                        "輸入錯誤",
                        f"無效的 Report ID HEX 數值: '{id_hex_str}'，請輸入 HEX 格式（例如 07 或 08）！",
                    )
                    return

            # 依據轉換選項打包 Request 封包
            request_bytes = self._prepare_payload(
                self.current_i_len, override_report_id=target_report_id
            )
            report_id = request_bytes[0]

            # 呼叫 Win32 HidD_GetInputReport 執行 Control Transfer 索取
            success, res = win32_get_input_report(
                self.connected_path, request_bytes, self.current_i_len
            )

            if success:
                recv_bytes = res
                ret_report_id = recv_bytes[0] if len(recv_bytes) > 0 else 0x00
                self.log(
                    f"[RX] Get Report (Control Pipe) 成功 (請求 ID: 0x{report_id:02X}, 回應 ID: 0x{ret_report_id:02X}, 長度: {len(recv_bytes)}/{self.current_i_len} Bytes):\n  <- {recv_bytes.hex(' ').upper()}"
                )
            else:
                self.log(f"[警告] Get Report (Control Pipe) 失敗: {res}")

        except Exception as e:
            self.log(f"[錯誤] Get Report 操作失敗: {str(e)}")

    def closeEvent(self, event):
        if self.monitor_thread:
            self.monitor_thread.stop()
            self.monitor_thread.wait()

        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
        event.accept()


# ---------------------------------------------------------------------------
# 主程式進入點
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        myappid = "neilxia.usbhidtool.gui.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)

    icon_path = get_resource_path("usb_hid_tool.ico")

    window = USBHIDApp()

    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
        window.setWindowIcon(app_icon)

    window.show()
    sys.exit(app.exec())
