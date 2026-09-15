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
# 資源路徑解析函數 (支援原本執行與 PyInstaller 打包後的單一 EXE 環境)
# ---------------------------------------------------------------------------
def get_resource_path(relative_path):
    """取得資源檔案的絕對路徑，兼容開發環境與 PyInstaller 打包環境"""
    if hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path


# ---------------------------------------------------------------------------
# Windows API 定義：讀取 HID Caps 取得 Input / Output / Feature 長度
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
        if ctypes.windll.hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed_data)):
            if ctypes.windll.hid.HidP_GetCaps(preparsed_data, ctypes.byref(caps)) == 0x00110000:
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

    path_str = device_path.decode("utf-8", errors="ignore") if isinstance(device_path, bytes) else str(device_path)

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
        err_code = ctypes.windll.kernel32.GetLastError()
        return False, f"CreateFileW 開啟失敗 (Win32 Error Code: {err_code})"

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
# 全時背景監測 Thread：採用 PyQt6 Signal 機制與 Debounce 機制
# ---------------------------------------------------------------------------
class USBGlobalMonitorThread(QThread):
    usb_changed = pyqtSignal()  # 裝置狀態穩定後觸發
    target_disconnected = pyqtSignal()  # 當前目標裝置拔除觸發

    def __init__(self, debounce_time=0.3):
        super().__init__()
        self.running = True
        self.target_path = None
        self.last_device_paths = set()

        # Debounce (防去抖動) 控制參數
        self.debounce_time = debounce_time
        self.pending_paths = None
        self.last_change_time = 0.0

    def set_target_path(self, path):
        self.target_path = path

    def clear_target_path(self):
        self.target_path = None

    def run(self):
        initial_devs = hid.enumerate()
        self.last_device_paths = {d.get("path") for d in initial_devs}

        while self.running:
            time.sleep(0.1)  # 縮短採樣週期以實現精確的 Debounce 計時
            if not self.running:
                break

            current_devs = hid.enumerate()
            current_paths = {d.get("path") for d in current_devs}
            now = time.time()

            # 1. 偵測硬體變動並重置 Debounce 計時器
            if current_paths != self.last_device_paths:
                if self.pending_paths != current_paths:
                    self.pending_paths = current_paths
                    self.last_change_time = now

            # 2. 只有在狀態持續穩定超過 debounce_time 後才發射 Signal
            if self.pending_paths is not None:
                if (now - self.last_change_time) >= self.debounce_time:
                    self.last_device_paths = self.pending_paths
                    self.pending_paths = None
                    self.usb_changed.emit()

            # 若當前有連線中的目標裝置，專門檢查該裝置是否仍然存在
            if self.target_path:
                if self.target_path not in current_paths:
                    self.target_disconnected.emit()
                    self.target_path = None  # 避免重複發送

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# GUI 主程式 (PyQt6 實現)
# ---------------------------------------------------------------------------
class USBHIDApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.dev = None
        self.connected_path = None
        self.monitor_thread = None
        self.device_info_list = []
        self.cmd_list = []  # [(cmd_name, hex_str), ...]
        self.stop_requested = False  # 控制 Run All 中止標記

        # 動態 Report 長度紀錄 (由 HIDP_CAPS 決定)
        self.dev_caps_i_len = 0
        self.dev_caps_o_len = 0
        self.dev_caps_f_len = 0

        self.initUI()
        self.start_global_monitor()

    def initUI(self):
        self.setWindowTitle("USB HID Tool")
        self.resize(800, 600)
        self.setMinimumSize(640, 480)

        main_layout = QVBoxLayout()

        # 1. 裝置選擇與連線區域
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

        # 2. CMD 清單載入、自動執行與 4 個 CheckBox 選擇區塊
        cmd_v_layout = QVBoxLayout()

        # 第一排：命令載入與執行按鈕
        r1_layout = QHBoxLayout()
        self.load_cmd_btn = QPushButton("Load CMD List")
        self.load_cmd_btn.clicked.connect(self.load_cmd_file)
        self.load_cmd_btn.setEnabled(False)  # 未連線前預設禁用

        self.cmd_combo = QComboBox()
        self.cmd_combo.addItem("-- 請先載入命令清單 --")
        self.cmd_combo.currentIndexChanged.connect(self.on_cmd_selected)
        self.cmd_combo.setEnabled(False)  # 防呆：未載入命令前禁止選擇

        self.run_one_btn = QPushButton("Run One")
        self.run_one_btn.clicked.connect(self.run_one)
        self.run_one_btn.setFixedWidth(70)
        self.run_one_btn.setEnabled(False)

        self.run_all_btn = QPushButton("Run All")
        self.run_all_btn.clicked.connect(self.run_all)
        self.run_all_btn.setFixedWidth(70)
        self.run_all_btn.setEnabled(False)

        self.stop_run_btn = QPushButton("Stop")
        self.stop_run_btn.clicked.connect(self.stop_run_all)
        self.stop_run_btn.setFixedWidth(70)
        self.stop_run_btn.setEnabled(False)

        r1_layout.addWidget(self.load_cmd_btn)
        r1_layout.addWidget(self.cmd_combo, stretch=1)
        r1_layout.addWidget(self.run_one_btn)
        r1_layout.addWidget(self.run_all_btn)
        r1_layout.addWidget(self.stop_run_btn)

        # 第二排：4 個勾選項 (Set/Get Feature/Report) 與延遲時間
        r2_layout = QHBoxLayout()
        lbl_batch = QLabel("批次執行項目:")

        self.chk_set_feature = QCheckBox("Set Feature")
        self.chk_get_feature = QCheckBox("Get Feature")
        self.chk_set_report = QCheckBox("Set Report")
        self.chk_get_report = QCheckBox("Get Report")

        # 未連線前預設取消勾選且禁用
        for chk in [self.chk_set_feature, self.chk_get_feature, self.chk_set_report, self.chk_get_report]:
            chk.setChecked(False)
            chk.setEnabled(False)

        action_delay_label = QLabel("執行項目間隔時間 (ms):")
        self.action_delay_input = QLineEdit("100")
        self.action_delay_input.setFixedWidth(50)
        self.action_delay_input.setEnabled(False)  # 未連線前預設禁用

        r2_layout.addWidget(lbl_batch)
        r2_layout.addWidget(self.chk_set_feature)
        r2_layout.addWidget(self.chk_get_feature)
        r2_layout.addWidget(self.chk_set_report)
        r2_layout.addWidget(self.chk_get_report)
        r2_layout.addWidget(action_delay_label)
        r2_layout.addWidget(self.action_delay_input)
        r2_layout.addStretch()

        cmd_v_layout.addLayout(r1_layout)
        cmd_v_layout.addLayout(r2_layout)
        main_layout.addLayout(cmd_v_layout)

        # 3. HEX 資料輸入與 Report ID 轉置設定區塊
        data_layout = QVBoxLayout()
        self.input_label = QLabel("HEX 資料輸入 (未滿長度自動補 0x00，超過則阻擋)")
        data_layout.addWidget(self.input_label)

        # 改用 QTextEdit 以支援多行 HEX 資料輸入，高度設定約顯示兩排
        self.hex_input = QTextEdit()
        self.hex_input.setFixedHeight(45)
        self.hex_input.setPlaceholderText("例如: 00 01 02 03 或 06 06 00 05 5A 02 00 23 2F")
        data_layout.addWidget(self.hex_input)

        # Report ID 自動轉換控制列
        convert_layout = QHBoxLayout()
        self.chk_auto_convert = QCheckBox("啟用 Get Report 封包自動轉換 Report ID:")
        self.chk_auto_convert.setChecked(False)  # 預設不勾選，由 update_ui_state 統一掌控
        self.chk_auto_convert.setEnabled(False)  # 未連線前禁用
        self.chk_auto_convert.toggled.connect(self.on_convert_toggled)

        convert_label = QLabel("0x")
        self.get_report_id_input = QLineEdit("07")
        self.get_report_id_input.setFixedWidth(40)
        self.get_report_id_input.setMaxLength(2)
        self.get_report_id_input.setEnabled(False)  # 未連線前禁用

        convert_layout.addWidget(self.chk_auto_convert)
        convert_layout.addWidget(convert_label)
        convert_layout.addWidget(self.get_report_id_input)
        convert_layout.addStretch()
        data_layout.addLayout(convert_layout)

        main_layout.addLayout(data_layout)

        # 4. 功能按鈕區域 (Set/Get Feature & Set/Get Report 4 個按鈕)
        btn_layout = QHBoxLayout()
        self.set_feature_btn = QPushButton("Set Feature")
        self.set_feature_btn.clicked.connect(self.set_feature)

        self.get_feature_btn = QPushButton("Get Feature")
        self.get_feature_btn.clicked.connect(self.get_feature)

        self.set_report_btn = QPushButton("Set Report")
        self.set_report_btn.clicked.connect(self.set_report)

        self.get_report_btn = QPushButton("Get Report")
        self.get_report_btn.clicked.connect(self.get_report)

        for btn in [self.set_feature_btn, self.get_feature_btn, self.set_report_btn, self.get_report_btn]:
            btn.setEnabled(False)
            btn_layout.addWidget(btn)

        main_layout.addLayout(btn_layout)

        # 5. 通訊日誌 (Log) 顯示區域
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
        main_layout.addWidget(self.log_text, stretch=1)

        # 設定主視窗 Central Widget
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        # 啟動時自動掃描一次裝置
        self.refresh_devices()

    # ---------------------------------------------------------------------------
    # UI 事件處理 (Report ID 自動轉置開關)
    # ---------------------------------------------------------------------------
    def on_convert_toggled(self, checked):
        """根據「自動Report ID轉換」勾選狀態與 CheckBox 本身啟用狀態連動決定輸入框是否可用"""
        self.get_report_id_input.setEnabled(self.chk_auto_convert.isEnabled() and checked)

    # ---------------------------------------------------------------------------
    # 全時背景監控管理
    # ---------------------------------------------------------------------------
    def start_global_monitor(self):
        self.monitor_thread = USBGlobalMonitorThread()
        self.monitor_thread.usb_changed.connect(self.on_usb_changed)
        self.monitor_thread.target_disconnected.connect(self.handle_unexpected_disconnect)
        self.monitor_thread.start()

    def on_usb_changed(self):
        if self.dev is None:
            self.log("[系統] 偵測到 USB 裝置變更，自動更新選單...")
            self.refresh_devices()

    def handle_unexpected_disconnect(self):
        if self.dev is None:
            return

        self.stop_requested = True  # 若正在 Run All 亦一併中斷
        if self.monitor_thread:
            self.monitor_thread.clear_target_path()

        try:
            if self.dev:
                self.dev.close()
        except Exception:
            pass

        self.dev = None
        self.connected_path = None
        self.dev_caps_i_len = 0
        self.dev_caps_o_len = 0
        self.dev_caps_f_len = 0

        # 重置 UI 按鈕狀態
        self.connect_btn.setText("Connect")
        self.device_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.stop_run_btn.setEnabled(False)

        self.input_label.setText("HEX 資料輸入 (未滿長度自動補 0x00，超過則阻擋)")
        self.update_ui_state()

        self.log("[系統] 警告: 當前連線的 USB 裝置已被拔除，已自動中斷連線！")

        QMessageBox.warning(self, "裝置拔除提示", "偵測到 USB 裝置已被拔除，系統已自動斷開連線！")
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

        vid_pid_prefix = "Disconnected"
        if self.dev and self.device_info_list:
            idx = self.device_combo.currentIndex()
            if 0 <= idx < len(self.device_info_list):
                dev_info = self.device_info_list[idx]
                vid = dev_info.get("vendor_id", 0)
                pid = dev_info.get("product_id", 0)
                vid_pid_prefix = f"{vid:04X}_{pid:04X}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"{vid_pid_prefix}_log_{timestamp}.txt"
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

    def _set_chk_state(self, chk: QCheckBox, supported: bool):
        """依據 CAPS 長度開關 CheckBox 狀態：若不支援則設為 unchecked + disabled"""
        chk.setEnabled(supported)
        if supported:
            chk.setChecked(True)
        else:
            chk.setChecked(False)

    def update_ui_state(self):
        """根據連線狀態、裝置 CAPS 長度與 CMD 列表，控制按鈕、CheckBox 與 Convert Layout 開關及勾選狀態"""
        is_connected = self.dev is not None
        has_commands = len(self.cmd_list) > 0

        # 與裝置連線狀態直連的基礎元件控制
        self.load_cmd_btn.setEnabled(is_connected)
        self.action_delay_input.setEnabled(is_connected)

        # 若未連線，其餘控制項全關且取消勾選
        if not is_connected:
            self.run_one_btn.setEnabled(False)
            self.run_all_btn.setEnabled(False)
            self.set_feature_btn.setEnabled(False)
            self.get_feature_btn.setEnabled(False)
            self.set_report_btn.setEnabled(False)
            self.get_report_btn.setEnabled(False)
            for chk in [self.chk_set_feature, self.chk_get_feature, self.chk_set_report, self.chk_get_report]:
                chk.setChecked(False)
                chk.setEnabled(False)

            # Convert Layout 區塊關閉並取消勾選
            self.chk_auto_convert.setChecked(False)
            self.chk_auto_convert.setEnabled(False)
            self.get_report_id_input.setEnabled(False)
            return

        # 根據 CAPS 長度決定各操作管道支援性 (F / O / I > 0)
        has_f = self.dev_caps_f_len > 0
        has_o = self.dev_caps_o_len > 0
        has_i = self.dev_caps_i_len > 0

        # 按鈕 Enable/Disable 判斷
        self.set_feature_btn.setEnabled(has_f)
        self.get_feature_btn.setEnabled(has_f)
        self.set_report_btn.setEnabled(has_o)
        self.get_report_btn.setEnabled(has_i)

        # CheckBox 同步判斷 Enable/Disable 與 Checked/Unchecked
        self._set_chk_state(self.chk_set_feature, has_f)
        self._set_chk_state(self.chk_get_feature, has_f)
        self._set_chk_state(self.chk_set_report, has_o)
        self._set_chk_state(self.chk_get_report, has_i)

        # 連動判斷 Convert Layout 區塊：必須「同時支援 Set Report (Output > 0) 與 Get Report (Input > 0)」
        supports_convert = has_o and has_i
        if supports_convert:
            self.chk_auto_convert.setEnabled(True)
            self.chk_auto_convert.setChecked(True)  # 預設帶出勾選
            self.get_report_id_input.setEnabled(True)
        else:
            self.chk_auto_convert.setChecked(False)
            self.chk_auto_convert.setEnabled(False)
            self.get_report_id_input.setEnabled(False)

        # 只要有一項長度支援即可啟用 Run One，Run All 需額外有命令清單
        any_valid_caps = has_f or has_o or has_i
        self.run_one_btn.setEnabled(any_valid_caps)
        self.run_all_btn.setEnabled(any_valid_caps and has_commands)

    def load_cmd_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "開啟命令清單檔案", "", "Text Files (*.txt);;All Files (*)")
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

            if count > 0:
                self.cmd_combo.setEnabled(True)
            else:
                self.cmd_combo.setEnabled(False)
                QMessageBox.warning(self, "警告", "檔案內未解析到符合 [Name],[HEX] 格式的命令！")

            self.update_ui_state()

        except Exception as e:
            self.cmd_combo.setEnabled(False)
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

    def _get_selected_batch_actions(self):
        """根據使用者勾選的 CheckBox 取得要執行的動作清單"""
        actions = []
        if self.chk_set_feature.isChecked() and self.chk_set_feature.isEnabled():
            actions.append(("Set Feature", self.set_feature))
        if self.chk_get_feature.isChecked() and self.chk_get_feature.isEnabled():
            actions.append(("Get Feature", self.get_feature))
        if self.chk_set_report.isChecked() and self.chk_set_report.isEnabled():
            actions.append(("Set Report", self.set_report))
        if self.chk_get_report.isChecked() and self.chk_get_report.isEnabled():
            actions.append(("Get Report", self.get_report))
        return actions

    def run_one(self):
        if not self.dev:
            return

        actions = self._get_selected_batch_actions()
        if not actions:
            QMessageBox.warning(self, "未選擇項目", "請至少勾選一個要執行的項目 (Set/Get Feature/Report)！")
            return

        try:
            delay_ms = float(self.action_delay_input.text().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            QMessageBox.warning(self, "輸入錯誤", "請輸入有效的延遲時間 (正數數字毫秒)！")
            return

        # 鎖定 UI 按鈕
        self.run_one_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)
        self.set_feature_btn.setEnabled(False)
        self.get_feature_btn.setEnabled(False)
        self.set_report_btn.setEnabled(False)
        self.get_report_btn.setEnabled(False)

        try:
            self.log("==========================================")
            self.log(f"[Run One] 開始執行單組命令 (包含 {len(actions)} 個勾選項，delay={delay_ms:.0f}ms)...")
            self.log("==========================================")

            for act_name, act_func in actions:
                act_func()
                QCoreApplication.processEvents()

                if delay_sec > 0:
                    self._safe_delay(delay_sec)

            self.log("==========================================")
            self.log("[Run One] 單組命令測試完成！")
            self.log("==========================================")

        finally:
            self.update_ui_state()

    def run_all(self):
        if not self.dev or not self.cmd_list:
            return

        actions = self._get_selected_batch_actions()
        if not actions:
            QMessageBox.warning(self, "未選擇項目", "請至少勾選一個要執行的項目 (Set/Get Feature/Report)！")
            return

        try:
            delay_ms = float(self.action_delay_input.text().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            QMessageBox.warning(self, "輸入錯誤", "請輸入有效的延遲時間 (正數數字毫秒)！")
            return

        self.stop_requested = False
        self.log("==========================================")
        self.log(f"[Run All] 開始執行批次測試，共 {len(self.cmd_list)} 項命令 (間隔 delay={delay_ms:.0f}ms)...")
        self.log("==========================================")

        self.run_one_btn.setEnabled(False)
        self.run_all_btn.setEnabled(False)
        self.stop_run_btn.setEnabled(True)
        self.set_feature_btn.setEnabled(False)
        self.get_feature_btn.setEnabled(False)
        self.set_report_btn.setEnabled(False)
        self.get_report_btn.setEnabled(False)

        total = len(self.cmd_list)
        executed_count = 0

        try:
            for idx, (name, hex_str) in enumerate(self.cmd_list, 1):
                if not self.dev:
                    break

                self.log(f">>> [{idx}/{total}] 執行命令: {name} (delay={delay_ms:.0f}ms)")

                self.cmd_combo.setCurrentIndex(idx)
                self.hex_input.setText(hex_str)
                QCoreApplication.processEvents()

                for act_name, act_func in actions:
                    if self.stop_requested or not self.dev:
                        break

                    act_func()
                    QCoreApplication.processEvents()

                    if delay_sec > 0:
                        self._safe_delay(delay_sec)

                executed_count += 1

                if self.stop_requested or not self.dev:
                    self.log("==========================================")
                    self.log(f"[Run All] 已依照請求完成第 {idx} 項命令後安全停止！")
                    break
        finally:
            self.stop_requested = False
            self.stop_run_btn.setEnabled(False)
            self.update_ui_state()

        self.log("==========================================")
        if self.stop_requested:
            self.log(f"[Run All] 批次命令已手動中斷！(共完成 {executed_count}/{total} 項命令)")
        else:
            self.log(f"[Run All] 批次命令測試完成！(共執行 {total} 項命令)")
        self.log("==========================================")

    def refresh_devices(self):
        self.device_combo.clear()
        raw_list = hid.enumerate()

        if not raw_list:
            self.device_combo.addItem("未找到任何 HID 裝置")
            self.log("[系統] 未偵測到任何 USB HID 裝置。")
            return

        # 1. Mask 過濾 Unknown 裝置
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

        # 2. 依 VID -> PID -> Product String 排序
        self.device_info_list.sort(
            key=lambda d: (
                d.get("vendor_id", 0),
                d.get("product_id", 0),
                (d.get("product_string") or "").lower(),
            )
        )

        # 3. 填入 ComboBox 介面
        for dev in self.device_info_list:
            vid = f"{dev['vendor_id']:04X}"
            pid = f"{dev['product_id']:04X}"
            mfg = dev.get("manufacturer_string") or "Unknown"
            prod = dev.get("product_string") or "Unknown"

            path = dev.get("path")
            if isinstance(path, bytes):
                path_str = path.decode("utf-8", errors="ignore")
            else:
                path_str = str(path)

            i_len, o_len, f_len = get_hid_report_lengths(path_str)
            display_str = f"[{vid}, {pid}] (I={i_len}, O={o_len}, F={f_len}) | {prod} ({mfg})"
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
                path_str = path.decode("utf-8", errors="ignore") if isinstance(path, bytes) else str(path)

                i_len, o_len, f_len = get_hid_report_lengths(path_str)

                # 若三個 Report 長度皆為 0，則拒絕連線
                if i_len == 0 and o_len == 0 and f_len == 0:
                    self.log("[錯誤] 拒絕連線：該裝置宣告之 Input/Output/Feature 長度皆為 0！")
                    QMessageBox.warning(
                        self,
                        "拒絕連線",
                        "該裝置宣告之 Input/Output/Feature 長度皆為 0，無法進行傳輸！",
                    )
                    return

                self.dev_caps_i_len = i_len
                self.dev_caps_o_len = o_len
                self.dev_caps_f_len = f_len

                self.dev = hid.device()
                self.dev.open_path(path)
                self.dev.set_nonblocking(True)
                self.connected_path = path

                # 通知監控 Thread 當前連線的裝置路徑
                if self.monitor_thread:
                    self.monitor_thread.set_target_path(self.connected_path)

                self.log(f"[連線] 成功連接至: VID={target_dev['vendor_id']:04X}&PID={target_dev['product_id']:04X}")
                self.log(f"[連線] 裝置預設封包長度 -> Input (I): {i_len}, Output (O): {o_len}, Feature (F): {f_len}")

                self.input_label.setText(
                    f"HEX 資料長度上限 -> Input: {i_len} B | Output: {o_len} B | Feature: {f_len} B"
                )

                self.connect_btn.setText("Disconnect")
                self.device_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)

            except Exception as e:
                self.log(f"[錯誤] 連接失敗: {str(e)}")
                QMessageBox.critical(self, "錯誤", f"無法連接至該裝置:\n{e}")
                self.dev = None
                self.connected_path = None
                self.dev_caps_i_len = 0
                self.dev_caps_o_len = 0
                self.dev_caps_f_len = 0
        else:
            if self.monitor_thread:
                self.monitor_thread.clear_target_path()

            try:
                self.dev.close()
            except Exception:
                pass
            self.dev = None
            self.connected_path = None
            self.dev_caps_i_len = 0
            self.dev_caps_o_len = 0
            self.dev_caps_f_len = 0
            self.log("[連線] 已中斷裝置連接。")
            self.connect_btn.setText("Connect")
            self.device_combo.setEnabled(True)
            self.refresh_btn.setEnabled(True)
            self.input_label.setText("HEX 資料輸入 (未滿長度自動補 0x00，超過則阻擋)")

        self.update_ui_state()

    # ---------------------------------------------------------------------------
    # 解析並驗證傳送的封包 (長度超過直接阻擋，未滿則自動補 0x00)
    # ---------------------------------------------------------------------------
    def _prepare_payload(self, target_len, override_report_id=None):
        if target_len <= 0:
            raise ValueError("該裝置不支援此 Report 類型 (宣告長度為 0 Bytes)！")

        raw_text = self.hex_input.toPlainText().strip().replace(" ", "")

        if not raw_text:
            raise ValueError("HEX 輸入欄位為空，請填入有效資料！")

        try:
            parsed_bytes = bytearray(bytes.fromhex(raw_text))
        except ValueError:
            raise ValueError("請輸入有效的 HEX 字串（例如：00 01 02 ...）！")

        if override_report_id is not None and len(parsed_bytes) > 0:
            parsed_bytes[0] = override_report_id

        if len(parsed_bytes) > target_len:
            raise ValueError(
                f"輸入資料長度 ({len(parsed_bytes)} Bytes) 已超過裝置預設長度 ({target_len} Bytes)！\n"
                f"為維護 Protocol 完整性，拒絕自動裁切並停止發送。"
            )

        padded_bytes = bytes(parsed_bytes).ljust(target_len, b"\x00")
        return padded_bytes

    def set_feature(self):
        if not self.dev:
            return

        try:
            payload = self._prepare_payload(self.dev_caps_f_len)
            report_id = payload[0]

            bytes_written = self.dev.send_feature_report(payload)
            if bytes_written > 0:
                self.log(
                    f"[TX] Set Feature 成功 (Report ID: 0x{report_id:02X}, 長度: {bytes_written}/{self.dev_caps_f_len} Bytes):\n  -> {payload.hex(' ').upper()}"
                )
            else:
                self.log("[錯誤] Set Feature 傳送失敗。")

        except Exception as e:
            self.log(f"[錯誤] Set Feature 操作失敗: {str(e)}")
            if "write" in str(e).lower() or "device" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "長度驗證失敗", str(e))

    def get_feature(self):
        if not self.dev:
            return

        try:
            payload = self._prepare_payload(self.dev_caps_f_len)
            report_id = payload[0]

            target_len = self.dev_caps_f_len
            response = self.dev.get_feature_report(report_id, target_len)

            if response:
                recv_bytes = bytes(response)
                self.log(
                    f"[RX] Get Feature 接收成功 (Report ID: 0x{report_id:02X}, 長度: {len(recv_bytes)}/{self.dev_caps_f_len} Bytes):\n  <- {recv_bytes.hex(' ').upper()}"
                )
            else:
                self.log(f"[警告] 未收到來自裝置的 Get Feature 回應 (Report ID: 0x{report_id:02X})。")

        except Exception as e:
            self.log(f"[錯誤] Get Feature 操作失敗: {str(e)}")
            if "read" in str(e).lower() or "device" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "長度驗證失敗", str(e))

    def set_report(self):
        if not self.dev:
            return

        try:
            payload = self._prepare_payload(self.dev_caps_o_len)
            report_id = payload[0]

            bytes_written = self.dev.write(payload)
            if bytes_written > 0:
                self.log(
                    f"[TX] Set Report 成功 (Report ID: 0x{report_id:02X}, 長度: {bytes_written}/{self.dev_caps_o_len} Bytes):\n  -> {payload.hex(' ').upper()}"
                )
            else:
                self.log("[錯誤] Set Report 傳送失敗。")

        except Exception as e:
            self.log(f"[錯誤] Set Report 操作失敗: {str(e)}")
            if "write" in str(e).lower() or "device" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "長度驗證失敗", str(e))

    def get_report(self):
        if not self.connected_path:
            return

        try:
            target_report_id = None

            # 雙重防呆：確保開關有開啟且元件被 Enable 時才做轉置
            if self.chk_auto_convert.isEnabled() and self.chk_auto_convert.isChecked():
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

            request_bytes = self._prepare_payload(self.dev_caps_i_len, override_report_id=target_report_id)
            report_id = request_bytes[0]

            success, res = win32_get_input_report(self.connected_path, request_bytes, self.dev_caps_i_len)

            if success:
                # 告訴靜態分析器此時 res 必為 bytes
                assert isinstance(res, bytes)
                recv_bytes = res
                ret_report_id = recv_bytes[0] if len(recv_bytes) > 0 else 0x00
                self.log(
                    f"[RX] Get Report 成功 (請求 ID: 0x{report_id:02X}, 回應 ID: 0x{ret_report_id:02X}, 長度: {len(recv_bytes)}/{self.dev_caps_i_len} Bytes):\n  <- {recv_bytes.hex(' ').upper()}"
                )
            else:
                self.log(f"[錯誤] Get Report 失敗: {res}")

        except Exception as e:
            self.log(f"[錯誤] Get Report 操作失敗: {str(e)}")
            if "device" in str(e).lower() or "handle" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "長度驗證失敗", str(e))

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
        myappid = "neilxia.usbhidtool.qt.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)
    window = USBHIDApp()

    icon_path = get_resource_path("usb_hid_tool.ico")
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))

    window.show()
    sys.exit(app.exec())
