import sys
import os
import time
import threading
import ctypes
import ctypes.wintypes as wt
from datetime import datetime
from pathlib import Path
import hid
import wx


# ---------------------------------------------------------------------------
# 資源路徑解析函數 (支援原本執行與 PyInstaller 打包後的單一 EXE 環境)
# ---------------------------------------------------------------------------
def get_resource_path(relative_path):
    """取得資源檔案的絕對路徑，兼容開發環境與 PyInstaller 打包環境"""
    if hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)
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


# ---------------------------------------------------------------------------
# 全時背景監測 Thread：採用 Callback + wx.CallAfter 實現跨執行緒解耦
# ---------------------------------------------------------------------------
class USBGlobalMonitorThread(threading.Thread):
    def __init__(self, on_usb_changed_cb, on_target_disconnected_cb):
        super().__init__()
        self.daemon = True
        self.running = True
        self.target_path = None
        self.last_device_paths = set()

        self.on_usb_changed_cb = on_usb_changed_cb
        self.on_target_disconnected_cb = on_target_disconnected_cb

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

            # 檢查是否有任何裝置新增或移除
            if current_paths != self.last_device_paths:
                self.last_device_paths = current_paths
                if self.on_usb_changed_cb:
                    wx.CallAfter(self.on_usb_changed_cb)

            # 若當前有連線中的目標裝置，專門檢查該裝置是否仍然存在
            if self.target_path:
                if self.target_path not in current_paths:
                    if self.on_target_disconnected_cb:
                        wx.CallAfter(self.on_target_disconnected_cb)
                    self.target_path = None  # 避免重複觸發

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# GUI 主視窗 (wxPython 現代化佈局與事件重構)
# ---------------------------------------------------------------------------
class USBHIDFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="USB HID Feature Report 工具", size=(850, 650))
        self.SetMinSize((800, 600))

        self.dev = None
        self.connected_path = None
        self.monitor_thread = None
        self.device_info_list = []
        self.cmd_list = []  # [(cmd_name, hex_str), ...]
        self.stop_requested = False  # 控制 Run All 中止標記

        # 動態 Report 長度紀錄 (由 HIDP_CAPS 決定)
        self.dev_caps_f_len = 0

        self.initUI()
        self.start_global_monitor()

    def initUI(self):
        """建立主視窗面板與區塊化佈局"""
        main_panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 依功能區塊獨立構建 UI 元件
        main_sizer.Add(self._build_device_section(main_panel), 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self._build_cmd_section(main_panel), 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self._build_data_section(main_panel), 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self._build_action_section(main_panel), 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self._build_log_section(main_panel), 1, wx.EXPAND | wx.ALL, 5)

        main_panel.SetSizer(main_sizer)

        # 視窗關閉事件綁定
        self.Bind(wx.EVT_CLOSE, self.on_close)

        # 啟動時自動掃描一次裝置
        self.refresh_devices()

    # ---------------------------------------------------------------------------
    # UI 區塊建構子與事件綁定
    # ---------------------------------------------------------------------------
    def _build_device_section(self, parent):
        """1. 裝置選擇與連線控制區塊"""
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.refresh_btn = wx.Button(parent, label="Refresh")
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_btn_refresh)

        self.device_combo = wx.ComboBox(parent, style=wx.CB_READONLY)

        self.connect_btn = wx.Button(parent, label="Connect")
        self.connect_btn.Bind(wx.EVT_BUTTON, self.on_btn_connect)

        sizer.Add(self.refresh_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.device_combo, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.connect_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        return sizer

    def _build_cmd_section(self, parent):
        """2. 命令清單載入與自動批次執行區塊"""
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.load_cmd_btn = wx.Button(parent, label="Load CMD List")
        self.load_cmd_btn.Bind(wx.EVT_BUTTON, self.on_btn_load_cmd)

        self.cmd_combo = wx.ComboBox(parent, style=wx.CB_READONLY)
        self.cmd_combo.Append("-- 請先載入命令清單 --")
        self.cmd_combo.SetSelection(0)
        self.cmd_combo.Bind(wx.EVT_COMBOBOX, self.on_cmd_selected)
        self.cmd_combo.Enable(False)

        action_delay_label = wx.StaticText(parent, label="Delay (ms):")
        self.action_delay_input = wx.TextCtrl(parent, value="100", size=(50, -1))

        self.run_one_btn = wx.Button(parent, label="Run One", size=(70, -1))
        self.run_one_btn.Bind(wx.EVT_BUTTON, self.on_btn_run_one)
        self.run_one_btn.Enable(False)

        self.run_all_btn = wx.Button(parent, label="Run All", size=(70, -1))
        self.run_all_btn.Bind(wx.EVT_BUTTON, self.on_btn_run_all)
        self.run_all_btn.Enable(False)

        self.stop_run_btn = wx.Button(parent, label="Stop", size=(70, -1))
        self.stop_run_btn.Bind(wx.EVT_BUTTON, self.on_btn_stop_run)
        self.stop_run_btn.Enable(False)

        sizer.Add(self.load_cmd_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.cmd_combo, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(action_delay_label, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.action_delay_input, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.run_one_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.run_all_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.stop_run_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        return sizer

    def _build_data_section(self, parent):
        """3. HEX 資料輸入區塊"""
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.input_label = wx.StaticText(parent, label="HEX 資料輸入 (未滿長度自動補 0x00，超過則阻擋)")
        sizer.Add(self.input_label, 0, wx.ALL, 5)

        self.hex_input = wx.TextCtrl(parent)
        self.hex_input.SetHint("例如: 00 01 02 03 (第一個 00 即為 Report ID)")
        sizer.Add(self.hex_input, 0, wx.EXPAND | wx.ALL, 5)
        return sizer

    def _build_action_section(self, parent):
        """4. Set Feature 及 Get Feature 操作按鈕區塊"""
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.set_feature_btn = wx.Button(parent, label="Set Feature")
        self.set_feature_btn.Bind(wx.EVT_BUTTON, self.on_btn_set_feature)
        self.set_feature_btn.Enable(False)

        self.get_feature_btn = wx.Button(parent, label="Get Feature")
        self.get_feature_btn.Bind(wx.EVT_BUTTON, self.on_btn_get_feature)
        self.get_feature_btn.Enable(False)

        sizer.Add(self.set_feature_btn, 1, wx.ALL, 5)
        sizer.Add(self.get_feature_btn, 1, wx.ALL, 5)
        return sizer

    def _build_log_section(self, parent):
        """5. 通訊日誌顯示與匯出控制區塊"""
        sizer = wx.BoxSizer(wx.VERTICAL)

        log_header_sizer = wx.BoxSizer(wx.HORIZONTAL)
        log_header_sizer.Add(wx.StaticText(parent, label="通訊日誌 (Log):"), 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        log_header_sizer.AddStretchSpacer(1)

        self.clear_log_btn = wx.Button(parent, label="Clear Log")
        self.clear_log_btn.Bind(wx.EVT_BUTTON, self.on_btn_clear_log)

        self.save_log_btn = wx.Button(parent, label="Save Log")
        self.save_log_btn.Bind(wx.EVT_BUTTON, self.on_btn_save_log)

        log_header_sizer.Add(self.clear_log_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        log_header_sizer.Add(self.save_log_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        sizer.Add(log_header_sizer, 0, wx.EXPAND | wx.ALL, 2)

        self.log_text = wx.TextCtrl(parent, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        sizer.Add(self.log_text, 1, wx.EXPAND | wx.ALL, 5)
        return sizer

    # ---------------------------------------------------------------------------
    # 按鈕事件處理控制點
    # ---------------------------------------------------------------------------
    def on_btn_refresh(self, event):
        self.refresh_devices()

    def on_btn_connect(self, event):
        self.toggle_connect()

    def on_btn_load_cmd(self, event):
        self.load_cmd_file()

    def on_btn_run_one(self, event):
        self.run_one()

    def on_btn_run_all(self, event):
        self.run_all()

    def on_btn_stop_run(self, event):
        self.stop_run_all()

    def on_btn_set_feature(self, event):
        self.set_feature()

    def on_btn_get_feature(self, event):
        self.get_feature()

    def on_btn_clear_log(self, event):
        self.clear_log()

    def on_btn_save_log(self, event):
        self.save_log()

    # ---------------------------------------------------------------------------
    # 全時背景監控管理 (CallAfter 核心)
    # ---------------------------------------------------------------------------
    def start_global_monitor(self):
        self.monitor_thread = USBGlobalMonitorThread(
            on_usb_changed_cb=self.on_usb_changed, on_target_disconnected_cb=self.handle_unexpected_disconnect
        )
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
        self.dev_caps_f_len = 0

        # 重置 UI 按鈕狀態
        self.connect_btn.SetLabel("Connect")
        self.device_combo.Enable(True)
        self.refresh_btn.Enable(True)
        self.set_feature_btn.Enable(False)
        self.get_feature_btn.Enable(False)
        self.stop_run_btn.Enable(False)
        self.input_label.SetLabel("HEX 資料輸入 (未滿長度自動補 0x00，超過則阻擋)")
        self.update_ui_state()

        self.log("[系統] 警告: 當前連線的 USB 裝置已被拔除，已自動中斷連線！")

        # 跳出彈窗告知使用者
        wx.MessageBox("偵測到 USB 裝置已被拔除，系統已自動斷開連線！", "裝置拔除提示", wx.OK | wx.ICON_WARNING, self)

        # 當使用者按下 OK 關閉視窗後，自動 Refresh 並更新選單
        self.refresh_devices()

    def log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S.%f]")[:-3] + "]"
        if "\n" in message:
            lines = message.split("\n")
            formatted_message = f"{timestamp} {lines[0]}"
            for line in lines[1:]:
                formatted_message += f"\n{timestamp} {line}"
            self.log_text.AppendText(formatted_message + "\n")
        else:
            self.log_text.AppendText(f"{timestamp} {message}\n")

    def clear_log(self):
        self.log_text.Clear()

    def save_log(self):
        log_content = self.log_text.GetValue()
        if not log_content.strip():
            wx.MessageBox("目前沒有任何 Log 紀錄可供儲存！", "提示", wx.OK | wx.ICON_INFORMATION, self)
            return

        vid_pid_prefix = "Disconnected"
        if self.dev and self.device_info_list:
            idx = self.device_combo.GetSelection()
            if 0 <= idx < len(self.device_info_list):
                dev_info = self.device_info_list[idx]
                vid = dev_info.get("vendor_id", 0)
                pid = dev_info.get("product_id", 0)
                vid_pid_prefix = f"{vid:04X}_{pid:04X}"

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"{vid_pid_prefix}_log_{timestamp}.txt"

        with wx.FileDialog(
            self,
            "儲存 Log 紀錄",
            defaultFile=default_filename,
            wildcard="Text Files (*.txt)|*.txt|Log Files (*.log)|*.log|All Files (*)|*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        ) as file_dialog:
            if file_dialog.ShowModal() == wx.ID_CANCEL:
                return
            file_path = file_dialog.GetPath()

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(log_content)
            self.log(f"[系統] Log 已成功匯出至: {file_path}")
            wx.MessageBox("Log 紀錄已成功儲存！", "成功", wx.OK | wx.ICON_INFORMATION, self)
        except Exception as e:
            wx.MessageBox(f"儲存檔案失敗:\n{e}", "錯誤", wx.OK | wx.ICON_ERROR, self)

    def update_ui_state(self):
        """根據連線狀態、裝置CAPS長度與 CMD 列表，控制 Run One 與 Run All 按鈕開關"""
        is_connected = self.dev is not None
        has_valid_lens = self.dev_caps_f_len > 0
        has_commands = len(self.cmd_list) > 0

        self.run_one_btn.Enable(is_connected and has_valid_lens)
        self.run_all_btn.Enable(is_connected and has_valid_lens and has_commands)

    def load_cmd_file(self):
        with wx.FileDialog(
            self,
            "開啟命令清單檔案",
            wildcard="Text Files (*.txt)|*.txt|All Files (*)|*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as file_dialog:
            if file_dialog.ShowModal() == wx.ID_CANCEL:
                return
            file_path = file_dialog.GetPath()

        try:
            self.cmd_list.clear()
            self.cmd_combo.Clear()
            self.cmd_combo.Append("-- 請選擇命令 --")

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
                        self.cmd_combo.Append(display_name)
                        count += 1

            self.cmd_combo.SetSelection(0)
            self.log(f"[命令載入] 成功載入 {count} 個命令。")

            # 防呆邏輯：當成功解析到至少一條命令時才啟用 ComboBox，否則保持禁用
            if count > 0:
                self.cmd_combo.Enable(True)
            else:
                self.cmd_combo.Enable(False)
                wx.MessageBox("檔案內未解析到符合 [Name],[HEX] 格式的命令！", "警告", wx.OK | wx.ICON_WARNING, self)

            self.update_ui_state()

        except Exception as e:
            self.cmd_combo.Enable(False)  # 讀檔發生例外時維持禁用
            self.log(f"[錯誤] 載入命令清單檔案失敗: {str(e)}")
            wx.MessageBox(f"讀取檔案失敗:\n{e}", "錯誤", wx.OK | wx.ICON_ERROR, self)

    def on_cmd_selected(self, event):
        index = self.cmd_combo.GetSelection()
        cmd_index = index - 1
        if 0 <= cmd_index < len(self.cmd_list):
            _, hex_str = self.cmd_list[cmd_index]
            self.hex_input.SetValue(hex_str)

    def stop_run_all(self):
        if not self.stop_requested:
            self.stop_requested = True
            self.stop_run_btn.Enable(False)
            self.log("[Run All] 收到使用者中斷請求，等待當前命令完成後將自動停止...")

    def _safe_delay(self, delay_sec):
        start_time = time.time()
        while (time.time() - start_time) < delay_sec:
            wx.Yield()
            time.sleep(0.005)

    def run_one(self):
        if not self.dev:
            return

        try:
            self._prepare_payload()
        except ValueError as e:
            wx.MessageBox(str(e), "長度驗證失敗", wx.OK | wx.ICON_WARNING, self)
            return

        try:
            delay_ms = float(self.action_delay_input.GetValue().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            wx.MessageBox("請輸入有效的延遲時間 (正數數字毫秒)！", "輸入錯誤", wx.OK | wx.ICON_WARNING, self)
            return

        self.run_one_btn.Enable(False)
        self.run_all_btn.Enable(False)
        self.set_feature_btn.Enable(False)
        self.get_feature_btn.Enable(False)

        try:
            self.log("==========================================")
            self.log(f"[Run One] 開始執行單組命令 (delay={delay_ms:.0f}ms)...")
            self.log("==========================================")

            self.set_feature()
            wx.Yield()

            if delay_sec > 0:
                self._safe_delay(delay_sec)

            self.get_feature()
            wx.Yield()

            if delay_sec > 0:
                self._safe_delay(delay_sec)

            self.log("==========================================")
            self.log("[Run One] 單組命令測試完成！")
            self.log("==========================================")

        finally:
            is_valid = self.dev_caps_f_len > 0
            self.set_feature_btn.Enable(is_valid)
            self.get_feature_btn.Enable(is_valid)
            self.update_ui_state()

    def run_all(self):
        if not self.dev or not self.cmd_list:
            return

        try:
            delay_ms = float(self.action_delay_input.GetValue().strip())
            if delay_ms < 0:
                raise ValueError
            delay_sec = delay_ms / 1000.0
        except ValueError:
            wx.MessageBox("請輸入有效的延遲時間 (正數數字毫秒)！", "輸入錯誤", wx.OK | wx.ICON_WARNING, self)
            return

        self.stop_requested = False
        self.log("==========================================")
        self.log(f"[Run All] 開始執行批次測試，共 {len(self.cmd_list)} 項命令 (間隔 delay={delay_ms:.0f}ms)...")
        self.log("==========================================")

        self.run_one_btn.Enable(False)
        self.run_all_btn.Enable(False)
        self.stop_run_btn.Enable(True)
        self.set_feature_btn.Enable(False)
        self.get_feature_btn.Enable(False)

        total = len(self.cmd_list)
        executed_count = 0

        try:
            for idx, (name, hex_str) in enumerate(self.cmd_list, 1):
                if not self.dev:
                    break

                self.log(f">>> [{idx}/{total}] 執行命令: {name} (delay={delay_ms:.0f}ms)")

                self.cmd_combo.SetSelection(idx)
                self.hex_input.SetValue(hex_str)
                wx.Yield()

                self.set_feature()
                wx.Yield()

                if delay_sec > 0:
                    self._safe_delay(delay_sec)

                self.get_feature()
                wx.Yield()

                executed_count += 1

                if delay_sec > 0:
                    self._safe_delay(delay_sec)

                if self.stop_requested or not self.dev:
                    self.log("==========================================")
                    self.log(f"[Run All] 已依照請求完成第 {idx} 項命令後安全停止！")
                    break
        finally:
            self.stop_requested = False
            self.stop_run_btn.Enable(False)
            is_valid = self.dev_caps_f_len > 0
            self.set_feature_btn.Enable(is_valid)
            self.get_feature_btn.Enable(is_valid)
            self.update_ui_state()

        self.log("==========================================")
        if self.stop_requested:
            self.log(f"[Run All] 批次命令已手動中斷！(共完成 {executed_count}/{total} 項命令)")
        else:
            self.log(f"[Run All] 批次命令測試完成！(共執行 {total} 項命令)")
        self.log("==========================================")

    def refresh_devices(self):
        self.device_combo.Clear()
        raw_list = hid.enumerate()

        if not raw_list:
            self.device_combo.Append("未找到任何 HID 裝置")
            self.device_combo.SetSelection(0)
            self.log("[系統] 未偵測到任何 USB HID 裝置。")
            return

        # 1. Mask 過濾 Unknown 裝置
        self.device_info_list = []
        for dev in raw_list:
            prod = dev.get("product_string")
            mfg = dev.get("manufacturer_string")

            # 若無名稱字串或全是空白，則視為 Unknown 裝置予以 Mask
            if not prod and not mfg:
                continue

            self.device_info_list.append(dev)

        if not self.device_info_list:
            self.device_combo.Append("未找到具名的 HID 裝置 (已過濾 Unknown)")
            self.device_combo.SetSelection(0)
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
            display_str = f"[{vid}, {pid}] (I: {i_len}, O: {o_len}, F: {f_len}) | {prod} ({mfg})"
            self.device_combo.Append(display_str)

        self.device_combo.SetSelection(0)
        masked_count = len(raw_list) - len(self.device_info_list)
        self.log(
            f"[系統] 掃描完成：共 {len(self.device_info_list)} 個有效 HID 裝置 (已排序，已自動遮罩 {masked_count} 個 Unknown 裝置)。"
        )

    def toggle_connect(self):
        if self.dev is None:
            idx = self.device_combo.GetSelection()
            if idx < 0 or not self.device_info_list:
                wx.MessageBox("請先選擇有效的 USB 裝置！", "警告", wx.OK | wx.ICON_WARNING, self)
                return

            target_dev = self.device_info_list[idx]
            try:
                path = target_dev["path"]
                path_str = path.decode("utf-8", errors="ignore") if isinstance(path, bytes) else str(path)

                # 開啟裝置前，先取得該裝置精確的 Report 長度
                _, _, f_len = get_hid_report_lengths(path_str)
                if f_len <= 0:
                    self.log(
                        f"[錯誤] 拒絕連線：該裝置宣告之長度不符合 Feature Report 傳輸條件 (Feature: {f_len})，必須不為 0！"
                    )
                    wx.MessageBox(
                        f"該裝置宣告之長度不符合 Feature Report 傳輸條件 (Feature: {f_len})，必須不為 0！",
                        "拒絕連線",
                        wx.OK | wx.ICON_WARNING,
                        self,
                    )
                    return

                self.dev_caps_f_len = f_len

                self.dev = hid.device()
                self.dev.open_path(path)
                self.dev.set_nonblocking(True)
                self.connected_path = path

                # 通知監控 Thread 當前連線的裝置路徑
                if self.monitor_thread:
                    self.monitor_thread.set_target_path(self.connected_path)

                self.log(f"[連線] 成功連接至: VID={target_dev['vendor_id']:04X}&PID={target_dev['product_id']:04X}")
                self.log(f"[連線] 裝置預設封包長度 -> Feature (F): {f_len}")

                self.input_label.SetLabel(f"HEX 資料輸入 Feature Report 預設封包長度: {f_len} Bytes")

                # 更新介面動態提示長度資訊
                self.input_label.SetLabel(f"HEX 資料輸入 Feature Report 預設封包長度: {f_len} Bytes")

                self.connect_btn.SetLabel("Disconnect")
                self.device_combo.Enable(False)
                self.refresh_btn.Enable(False)
                self.set_feature_btn.Enable(True)
                self.get_feature_btn.Enable(True)

            except Exception as e:
                self.log(f"[錯誤] 連接失敗: {str(e)}")
                wx.MessageBox(f"無法連接至該裝置:\n{e}", "錯誤", wx.OK | wx.ICON_ERROR, self)
                self.dev = None
                self.connected_path = None
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
            self.dev_caps_f_len = 0
            self.log("[連線] 已中斷裝置連接。")
            self.connect_btn.SetLabel("Connect")
            self.device_combo.Enable(True)
            self.refresh_btn.Enable(True)
            self.set_feature_btn.Enable(False)
            self.get_feature_btn.Enable(False)
            self.input_label.SetLabel("HEX 資料輸入 (未滿長度自動補 0x00，超過則阻擋)")

        self.update_ui_state()

    # ---------------------------------------------------------------------------
    # 解析並驗證傳送的封包 (長度超過直接阻擋，未滿則自動補 0x00)
    # ---------------------------------------------------------------------------
    def _prepare_payload(self):
        if self.dev_caps_f_len <= 0:
            raise ValueError("當前裝置不支援 Feature Report (長度為 0 Bytes)！")

        raw_text = self.hex_input.GetValue().strip().replace(" ", "")

        if not raw_text:
            raise ValueError("HEX 輸入欄位為空，請填入有效資料！")

        try:
            parsed_bytes = bytearray(bytes.fromhex(raw_text))
        except ValueError:
            raise ValueError("請輸入有效的 HEX 字串（例如：00 01 02 ...）！")

        target_len = self.dev_caps_f_len

        # 若超過裝置預設長度：嚴格阻擋並丟出例外，拒絕發送無效/不完整封包
        if len(parsed_bytes) > target_len:
            raise ValueError(
                f"輸入資料長度 ({len(parsed_bytes)} Bytes) 已超過裝置預設長度 ({target_len} Bytes)！\n"
                f"為維護 Protocol 完整性，拒絕自動裁切並停止發送。"
            )

        # 若未滿預設長度：自動向後補 0x00 填滿至裝置預設長度
        padded_bytes = bytes(parsed_bytes).ljust(target_len, b"\x00")
        return padded_bytes

    def set_feature(self):
        if not self.dev:
            return

        try:
            payload = self._prepare_payload()
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
                wx.MessageBox(str(e), "長度驗證失敗", wx.OK | wx.ICON_WARNING, self)

    def get_feature(self):
        if not self.dev:
            return

        try:
            # 透過 _prepare_payload 進行 HEX 格式合法性與長度檢查
            payload = self._prepare_payload()
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
                wx.MessageBox(str(e), "長度驗證失敗", wx.OK | wx.ICON_WARNING, self)

    def on_close(self, event):
        if self.monitor_thread:
            self.monitor_thread.stop()

        if self.dev:
            try:
                self.dev.close()
            except Exception:
                pass
        self.Destroy()


# ---------------------------------------------------------------------------
# 主程式進入點
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        myappid = "neilxia.usbhidfeaturetool.wx.1.0"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = wx.App(False)
    frame = USBHIDFrame()

    icon_path = get_resource_path("usb_hid_tool.ico")
    if icon_path.exists():
        icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_ICO)
        frame.SetIcon(icon)

    frame.Show()
    app.MainLoop()
