import sys
import os
import time
import ctypes
import ctypes.wintypes as wt
from datetime import datetime
from pathlib import Path
import hid

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QComboBox, QLineEdit, QTextEdit, QLabel, QMessageBox, QFileDialog
)
from PyQt6.QtCore import QCoreApplication, QThread, pyqtSignal
from PyQt6.QtGui import QIcon

# ---------------------------------------------------------------------------
# 資源路徑解析函數 (支援原本執行與 PyInstaller 打包後的單一 EXE 環境)
# ---------------------------------------------------------------------------
def get_resource_path(relative_path):
    """取得資源檔案的絕對路徑，兼容開發環境與 PyInstaller 打包環境"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包後的臨時解壓目錄
        base_path = Path(sys._MEIPASS)
    else:
        # 一般 Python 執行時的腳本目錄
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path


# ---------------------------------------------------------------------------
# Windows API 定義：用來讀取 HID Caps 取得 Input / Output / Feature 長度
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
        None
    )

    if handle == -1 or handle == 0xFFFFFFFF:
        handle = ctypes.windll.kernel32.CreateFileW(
            device_path,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None
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


# ---------------------------------------------------------------------------
# 全時背景監測 Thread：負責自動偵測 USB 裝置的新增與移除
# ---------------------------------------------------------------------------
class USBGlobalMonitorThread(QThread):
    usb_changed = pyqtSignal()  # 只要有裝置插拔就觸發
    target_disconnected = pyqtSignal()  # 連線中的目標裝置被拔除時觸發

    def __init__(self):
        super().__init__()
        self.running = True
        self.target_path = None
        self.last_device_paths = set()

    def set_target_path(self, path):
        """設定當前已連線的目標裝置路徑"""
        self.target_path = path

    def clear_target_path(self):
        """清除連線目標"""
        self.target_path = None

    def run(self):
        initial_devs = hid.enumerate()
        self.last_device_paths = {d.get('path') for d in initial_devs}

        while self.running:
            time.sleep(0.5)
            if not self.running:
                break

            current_devs = hid.enumerate()
            current_paths = {d.get('path') for d in current_devs}

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
        
        self.initUI()
        self.start_global_monitor()

    def initUI(self):
        self.setWindowTitle("USB HID Feature Report 工具")
        self.resize(760, 580)

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
        self.cmd_combo.addItem("-- 請選擇或載入指令 (Custom Input) --")
        self.cmd_combo.currentIndexChanged.connect(self.on_cmd_selected)

        delay_label = QLabel("Delay (ms):")
        self.delay_input = QLineEdit("100")
        self.delay_input.setFixedWidth(50)

        self.auto_run_btn = QPushButton("Auto Run All")
        self.auto_run_btn.clicked.connect(self.auto_run_all)
        self.auto_run_btn.setEnabled(False)

        self.stop_run_btn = QPushButton("Stop")
        self.stop_run_btn.clicked.connect(self.stop_auto_run)
        self.stop_run_btn.setEnabled(False)

        cmd_layout.addWidget(self.load_cmd_btn)
        cmd_layout.addWidget(self.cmd_combo, stretch=1)
        cmd_layout.addWidget(delay_label)
        cmd_layout.addWidget(self.delay_input)
        cmd_layout.addWidget(self.auto_run_btn)
        cmd_layout.addWidget(self.stop_run_btn)
        main_layout.addLayout(cmd_layout)

        # 3. HEX 資料輸入區域
        data_layout = QVBoxLayout()
        data_layout.addWidget(QLabel("HEX 資料輸入 (首位為 Report ID，總長 64 Bytes，未滿自動補 00):"))
        
        self.hex_input = QLineEdit()
        self.hex_input.setPlaceholderText("例如: 00 01 02 03 (第一個 00 即為 Report ID)")
        data_layout.addWidget(self.hex_input)
        main_layout.addLayout(data_layout)

        # 4. 功能按鈕區域
        btn_layout = QHBoxLayout()
        self.set_feature_btn = QPushButton("Set Feature")
        self.set_feature_btn.clicked.connect(self.set_feature)
        self.set_feature_btn.setEnabled(False)

        self.get_feature_btn = QPushButton("Get Feature")
        self.get_feature_btn.clicked.connect(self.get_feature)
        self.get_feature_btn.setEnabled(False)

        btn_layout.addWidget(self.set_feature_btn)
        btn_layout.addWidget(self.get_feature_btn)
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
        self.set_feature_btn.setEnabled(False)
        self.get_feature_btn.setEnabled(False)
        self.stop_run_btn.setEnabled(False)
        self.update_auto_run_state()

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
            self, "儲存 Log 紀錄", default_filename, "Text Files (*.txt);;Log Files (*.log);;All Files (*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(log_content)
            self.log(f"[系統] Log 已成功匯出至: {file_path}")
            QMessageBox.information(self, "成功", "Log 紀錄已成功儲存！")
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"儲存檔案失敗:\n{e}")

    def update_auto_run_state(self):
        is_connected = self.dev is not None
        has_commands = len(self.cmd_list) > 0
        self.auto_run_btn.setEnabled(is_connected and has_commands)

    def load_cmd_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "開啟指令清單檔案", "", "Text Files (*.txt);;All Files (*)"
        )
        if not file_path:
            return

        try:
            self.cmd_list.clear()
            self.cmd_combo.clear()
            self.cmd_combo.addItem("-- 請選擇指令 --")

            count = 0
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or not line.startswith('['):
                        continue

                    parts = line.split('],[' if '],[' in line else '],[')
                    if len(parts) == 2:
                        cmd_name = parts[0].lstrip('[').strip()
                        hex_str = parts[1].rstrip(']').strip()

                        self.cmd_list.append((cmd_name, hex_str))
                        
                        display_name = f"#{count+1} {cmd_name}"
                        self.cmd_combo.addItem(display_name)
                        count += 1

            self.log(f"[指令載入] 成功載入 {count} 個指令檔內容。")
            if count == 0:
                QMessageBox.warning(self, "警告", "檔案內未解析到符合 [Name],[HEX] 格式的指令！")

            self.update_auto_run_state()

        except Exception as e:
            self.log(f"[錯誤] 載入指令檔失敗: {str(e)}")
            QMessageBox.critical(self, "錯誤", f"讀取檔案失敗:\n{e}")

    def on_cmd_selected(self, index):
        cmd_index = index - 1
        if 0 <= cmd_index < len(self.cmd_list):
            _, hex_str = self.cmd_list[cmd_index]
            self.hex_input.setText(hex_str)

    def stop_auto_run(self):
        if not self.stop_requested:
            self.stop_requested = True
            self.stop_run_btn.setEnabled(False)
            self.log("[Auto Run] 收到使用者中斷請求，等待當前指令 (Set/Get Feature) 完成後將自動停止...")

    def _safe_delay(self, delay_sec):
        start_time = time.time()
        while (time.time() - start_time) < delay_sec:
            QCoreApplication.processEvents()
            time.sleep(0.005)

    def auto_run_all(self):
        if not self.dev or not self.cmd_list:
            return

        try:
            delay_ms = float(self.delay_input.text().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            QMessageBox.warning(self, "輸入錯誤", "請輸入有效的延遲時間 (正數數字毫秒)！")
            return

        self.stop_requested = False
        self.log("==========================================")
        self.log(f"[Auto Run] 開始執行批次測試，共 {len(self.cmd_list)} 項指令 (間隔 delay={delay_ms:.0f}ms)...")
        self.log("==========================================")

        self.auto_run_btn.setEnabled(False)
        self.stop_run_btn.setEnabled(True)
        self.set_feature_btn.setEnabled(False)
        self.get_feature_btn.setEnabled(False)

        total = len(self.cmd_list)
        executed_count = 0

        for idx, (name, hex_str) in enumerate(self.cmd_list, 1):
            if not self.dev:
                break

            self.log(f">>> [{idx}/{total}] 執行指令: {name} (delay={delay_ms:.0f}ms)")
            
            self.cmd_combo.setCurrentIndex(idx)
            self.hex_input.setText(hex_str)
            QCoreApplication.processEvents()

            self.set_feature()
            QCoreApplication.processEvents()
            
            if delay_sec > 0:
                self._safe_delay(delay_sec)

            self.get_feature()
            QCoreApplication.processEvents()

            executed_count += 1

            if delay_sec > 0:
                self._safe_delay(delay_sec)

            if self.stop_requested or not self.dev:
                self.log("------------------------------------------")
                self.log(f"[Auto Run] 已依照請求完成第 {idx} 項指令後安全停止！")
                break

        self.log("==========================================")
        if self.stop_requested:
            self.log(f"[Auto Run] 批次指令已手動中斷！(共完成 {executed_count}/{total} 項指令)")
        else:
            self.log(f"[Auto Run] 批次指令測試完成！(共執行 {total} 項指令)")
        self.log("==========================================")

        self.stop_requested = False
        self.stop_run_btn.setEnabled(False)
        self.set_feature_btn.setEnabled(True)
        self.get_feature_btn.setEnabled(True)
        self.update_auto_run_state()

    def refresh_devices(self):
        self.device_combo.clear()
        raw_list = hid.enumerate()

        if not raw_list:
            self.device_combo.addItem("未找到任何 HID 裝置")
            self.log("[系統] 未偵測到任何 USB HID 裝置。")
            return

        self.device_info_list = []
        for dev in raw_list:
            prod = dev.get('product_string')
            mfg = dev.get('manufacturer_string')
            
            if not prod and not mfg:
                continue
            
            self.device_info_list.append(dev)

        if not self.device_info_list:
            self.device_combo.addItem("未找到具名的 HID 裝置 (已過濾 Unknown)")
            self.log("[系統] 掃描完成，但所有裝置皆為 Unknown 並已自動過濾。")
            return

        self.device_info_list.sort(key=lambda d: (
            d.get('vendor_id', 0),
            d.get('product_id', 0),
            (d.get('product_string') or '').lower()
        ))

        for dev in self.device_info_list:
            vid = f"{dev['vendor_id']:04X}"
            pid = f"{dev['product_id']:04X}"
            mfg = dev.get('manufacturer_string') or "Unknown"
            prod = dev.get('product_string') or "Unknown"
            
            path = dev.get('path')
            if isinstance(path, bytes):
                path_str = path.decode('utf-8', errors='ignore')
            else:
                path_str = str(path)

            i_len, o_len, f_len = get_hid_report_lengths(path_str)
            display_str = f"[{vid}, {pid}] (I: {i_len}, O: {o_len}, F: {f_len}) | {prod} ({mfg})"
            self.device_combo.addItem(display_str)

        masked_count = len(raw_list) - len(self.device_info_list)
        self.log(f"[系統] 掃描完成：共 {len(self.device_info_list)} 個有效 HID 裝置 (已排序，已自動遮罩 {masked_count} 個 Unknown 裝置)。")

    def toggle_connect(self):
        if self.dev is None:
            idx = self.device_combo.currentIndex()
            if idx < 0 or not self.device_info_list:
                QMessageBox.warning(self, "警告", "請先選擇有效的 USB 裝置！")
                return

            target_dev = self.device_info_list[idx]
            try:
                self.dev = hid.device()
                self.dev.open_path(target_dev['path'])
                self.dev.set_nonblocking(True)
                self.connected_path = target_dev['path']

                if self.monitor_thread:
                    self.monitor_thread.set_target_path(self.connected_path)

                self.log(f"[連線] 成功連接至: VID_{target_dev['vendor_id']:04X}&PID_{target_dev['product_id']:04X}")
                self.connect_btn.setText("Disconnect")
                self.device_combo.setEnabled(False)
                self.refresh_btn.setEnabled(False)
                self.set_feature_btn.setEnabled(True)
                self.get_feature_btn.setEnabled(True)

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
            self.set_feature_btn.setEnabled(False)
            self.get_feature_btn.setEnabled(False)

        self.update_auto_run_state()

    def _prepare_payload(self):
        raw_text = self.hex_input.text().strip().replace(" ", "")
        
        if not raw_text:
            raise ValueError("請輸入 HEX 字串！")

        try:
            parsed_bytes = bytes.fromhex(raw_text)
        except ValueError:
            raise ValueError("請輸入有效的 HEX 字串（例如：00 01 02 或 000102）！")

        if len(parsed_bytes) > 64:
            raise ValueError(f"輸入資料超過 64 Bytes（目前：{len(parsed_bytes)} Bytes）！")

        padded_bytes = parsed_bytes.ljust(64, b'\x00')
        return padded_bytes

    def set_feature(self):
        if not self.dev:
            return

        try:
            payload = self._prepare_payload()
            report_id = payload[0]
            
            bytes_written = self.dev.send_feature_report(payload)
            if bytes_written > 0:
                self.log(f"[TX] Set Feature 成功 (Report ID: 0x{report_id:02X}, 長度: {bytes_written} Bytes):\n  -> {payload.hex(' ')}")
            else:
                self.log("[錯誤] Set Feature 傳送失敗。")

        except Exception as e:
            self.log(f"[錯誤] Set Feature 操作失敗: {str(e)}")
            if "write" in str(e).lower() or "device" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "錯誤", str(e))

    def get_feature(self):
        if not self.dev:
            return

        try:
            raw_text = self.hex_input.text().strip().replace(" ", "")
            report_id = 0x00
            
            if raw_text:
                try:
                    parsed = bytes.fromhex(raw_text)
                    if len(parsed) > 0:
                        report_id = parsed[0]
                except ValueError:
                    pass

            response = self.dev.get_feature_report(report_id, 64)
            
            if response:
                recv_bytes = bytes(response)
                self.log(f"[RX] Get Feature 接收成功 (Report ID: 0x{report_id:02X}, 長度: {len(recv_bytes)} Bytes):\n  <- {recv_bytes.hex(' ')}")
            else:
                self.log(f"[警告] 未收到來自裝置的 Get Feature 回應 (Report ID: 0x{report_id:02X})。")

        except Exception as e:
            self.log(f"[錯誤] Get Feature 操作失敗: {str(e)}")
            if "read" in str(e).lower() or "device" in str(e).lower():
                self.handle_unexpected_disconnect()
            else:
                QMessageBox.warning(self, "錯誤", str(e))

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
        myappid = 'myfirmware.usbhidtool.gui.1.0'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)

    # 透過動態解析函數取得 .ico 絕對路徑
    icon_path = get_resource_path("usb_hid_feature_tool.ico")

    window = USBHIDApp()

    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)
        window.setWindowIcon(app_icon)

    window.show()
    sys.exit(app.exec())