import sys
import os
from datetime import datetime
from pathlib import Path
import wx
import wx._xml
import wx.richtext as rt


# ---------------------------------------------------------------------------
# 資源路徑解析函數 (相容 PyInstaller 打包環境)
# ---------------------------------------------------------------------------
def get_resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        base_path = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base_path = Path(__file__).resolve().parent
    return base_path / relative_path


# ---------------------------------------------------------------------------
# GUI 主視窗
# ---------------------------------------------------------------------------
class CmdLengthCheckerFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="CMD Payload 長度檢查工具", size=wx.Size(950, 700))
        self.SetMinSize(wx.Size(750, 550))

        self.cmd_list = []  # [(cmd_name, hex_str), ...]

        self.initUI()

    def initUI(self):
        """建立主視窗面板與區塊化佈局"""
        main_panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 依功能區塊獨立構建 UI 元件
        main_sizer.Add(self._build_top_control_section(main_panel), 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self._build_param_section(main_panel), 0, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(self._build_output_section(main_panel), 1, wx.EXPAND | wx.ALL, 5)

        main_panel.SetSizer(main_sizer)

        # 底部狀態列
        self.status_bar = self.CreateStatusBar()
        self.status_bar.SetStatusText("請點擊 [Load Cmd List] 載入命令檔案...")

    # ---------------------------------------------------------------------------
    # UI 區塊建構子 (模組化設計)
    # ---------------------------------------------------------------------------
    def _build_top_control_section(self, parent):
        """1. 頂部按鈕與功能設定區塊"""
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.load_cmd_btn = wx.Button(parent, label="Load Cmd List")
        self.load_cmd_btn.Bind(wx.EVT_BUTTON, self.on_btn_load_cmd)

        self.check_len_btn = wx.Button(parent, label="Payload 長度檢查")
        self.check_len_btn.Bind(wx.EVT_BUTTON, self.on_btn_check_length)
        self.check_len_btn.Enable(False)

        self.chk_big_endian = wx.CheckBox(parent, label="長度欄位數值為 Big-Endian 排列")

        sizer.Add(self.load_cmd_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.check_len_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.AddSpacer(20)
        sizer.Add(self.chk_big_endian, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        return sizer

    def _build_param_section(self, parent):
        """2. 檢查參數設定區塊 (長度偏移、大小、資料偏移)"""
        static_box = wx.StaticBox(parent, label="檢查參數設定")
        sizer = wx.StaticBoxSizer(static_box, wx.HORIZONTAL)

        lbl_len_offset = wx.StaticText(static_box, label="長度欄位偏移 (Offset):")
        self.txt_len_offset = wx.TextCtrl(static_box, value="1", size=wx.Size(45, -1))

        lbl_len_length = wx.StaticText(static_box, label="長度欄位大小 (Bytes):")
        self.txt_len_length = wx.TextCtrl(static_box, value="1", size=wx.Size(45, -1))

        lbl_payload_offset = wx.StaticText(static_box, label="Payload 欄位偏移 (Offset):")
        self.txt_payload_offset = wx.TextCtrl(static_box, value="3", size=wx.Size(45, -1))

        sizer.Add(lbl_len_offset, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.txt_len_offset, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.AddSpacer(15)

        sizer.Add(lbl_len_length, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.txt_len_length, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.AddSpacer(15)

        sizer.Add(lbl_payload_offset, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)
        sizer.Add(self.txt_payload_offset, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        return sizer

    def _build_output_section(self, parent):
        """3. 左右雙 Output 顯示區塊 (左側：已載入命令清單，右側：Payload 長度檢查結果)"""
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        # 左側輸出框 (Loaded Commands)
        left_box = wx.StaticBox(parent, label="已載入命令清單 (Loaded Commands)")
        left_sizer = wx.StaticBoxSizer(left_box, wx.VERTICAL)
        self.txt_left_output = rt.RichTextCtrl(left_box, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        left_sizer.Add(self.txt_left_output, 1, wx.EXPAND | wx.ALL, 5)

        # 右側輸出框 (Payload Length Check Results)
        right_box = wx.StaticBox(parent, label="Payload 長度檢查結果 (Payload Length Check Results)")
        right_sizer = wx.StaticBoxSizer(right_box, wx.VERTICAL)
        self.txt_right_output = rt.RichTextCtrl(right_box, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        right_sizer.Add(self.txt_right_output, 1, wx.EXPAND | wx.ALL, 5)

        # 設定字型為 Consolas 等寬字型
        font = wx.Font(10, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        font.SetFaceName("Consolas")
        self.txt_left_output.SetFont(font)
        self.txt_right_output.SetFont(font)

        sizer.Add(left_sizer, 1, wx.EXPAND | wx.ALL, 5)
        sizer.Add(right_sizer, 1, wx.EXPAND | wx.ALL, 5)

        return sizer

    # ---------------------------------------------------------------------------
    # 按鈕事件與業務邏輯
    # ---------------------------------------------------------------------------
    def on_btn_load_cmd(self, event):
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
            self.txt_left_output.Clear()
            self.txt_right_output.Clear()

            count = 0
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    # 依照 [CMD Name],[CMD] 格式解析
                    parts = line.split("],[")
                    if len(parts) == 2:
                        cmd_name = parts[0].lstrip("[").strip()
                        hex_str = parts[1].rstrip("]").strip()
                        self.cmd_list.append((cmd_name, hex_str))
                        count += 1

            if count > 0:
                self.check_len_btn.Enable(True)
                self._display_loaded_commands()
                msg = f"成功載入 {count} 個命令。"
                self.status_bar.SetStatusText(msg)
                wx.MessageBox(msg, "載入成功", wx.OK | wx.ICON_INFORMATION, self)
            else:
                self.check_len_btn.Enable(False)
                wx.MessageBox("檔案內未解析到符合 [CMD Name],[CMD] 格式的命令！", "警告", wx.OK | wx.ICON_WARNING, self)

        except Exception as e:
            self.check_len_btn.Enable(False)
            wx.MessageBox(f"讀取檔案失敗:\n{e}", "錯誤", wx.OK | wx.ICON_ERROR, self)

    def _display_loaded_commands(self):
        self.txt_left_output.Clear()
        self.txt_left_output.BeginTextColour(wx.BLACK)
        for idx, (name, hex_str) in enumerate(self.cmd_list, 1):
            self.txt_left_output.WriteText(f"#{idx:03d} [{name}]\n      {hex_str}\n\n")
        self.txt_left_output.EndTextColour()

    def on_btn_check_length(self, event):
        if not self.cmd_list:
            return

        try:
            len_offset = int(self.txt_len_offset.GetValue().strip())
            len_length = int(self.txt_len_length.GetValue().strip())
            payload_offset = int(self.txt_payload_offset.GetValue().strip())
            is_big_endian = self.chk_big_endian.IsChecked()
        except ValueError:
            wx.MessageBox("請輸入有效的整數數字作為偏移量與大小！", "輸入錯誤", wx.OK | wx.ICON_WARNING, self)
            return

        self.txt_right_output.Clear()
        mismatch_count = 0

        for idx, (name, hex_str) in enumerate(self.cmd_list, 1):
            clean_hex = hex_str.replace(" ", "")
            try:
                cmd_bytes = bytes.fromhex(clean_hex)
            except ValueError:
                self._write_error_result(f"#{idx:03d} [{name}]", "HEX 字串格式錯誤！", hex_str)
                mismatch_count += 1
                continue

            # 1. 檢查並解析長度欄位數值 (Length Field Value)
            if len(cmd_bytes) < (len_offset + len_length):
                self._write_error_result(
                    f"#{idx:03d} [{name}]",
                    f"封包長度 ({len(cmd_bytes)}B) 小於長度欄位偏移與大小之和 ({len_offset + len_length}B)",
                    hex_str,
                )
                mismatch_count += 1
                continue

            len_bytes = cmd_bytes[len_offset : len_offset + len_length]
            byte_order = "big" if is_big_endian else "little"
            expected_len = int.from_bytes(len_bytes, byte_order)

            # 2. 修正後的長度計算邏輯：從「資料欄位偏移」計算到結尾 (Payload 到 End)
            if len(cmd_bytes) < payload_offset:
                actual_payload_len = 0
            else:
                actual_payload_len = len(cmd_bytes) - payload_offset

            # 3. 比對計算出來的長度是否相等於長度欄位數值
            if actual_payload_len != expected_len:
                mismatch_count += 1
                detail = (
                    f"長度不符！長度欄位數值: {expected_len} Bytes | "
                    f"實際 Payload 長度 (從偏移到結尾): {actual_payload_len} Bytes"
                )
                self._write_error_result(f"#{idx:03d} [{name}]", detail, hex_str)

        if mismatch_count == 0:
            self.txt_right_output.BeginTextColour(wx.Colour(0, 128, 0))
            self.txt_right_output.WriteText(
                "🎉 檢查完成：所有命令的 Length Field Value 皆與實際 Payload 長度完全相符！"
            )
            self.txt_right_output.EndTextColour()
            self.status_bar.SetStatusText("長度檢查完成：所有命令皆正確。")
        else:
            self.status_bar.SetStatusText(f"長度檢查完成：共發現 {mismatch_count} 項長度不符的命令。")

    def _write_error_result(self, title, detail, hex_str):
        self.txt_right_output.BeginTextColour(wx.RED)
        self.txt_right_output.WriteText(f"❌ {title}\n")
        self.txt_right_output.BeginTextColour(wx.BLACK)
        self.txt_right_output.WriteText(f"    原因: {detail}\n")
        self.txt_right_output.WriteText(f"    Payload: {hex_str}\n\n")


# ---------------------------------------------------------------------------
# 主程式進入點
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = wx.App(False)
    frame = CmdLengthCheckerFrame()

    icon_path = get_resource_path("usb_hid_tool.ico")
    if icon_path.exists():
        icon = wx.Icon(str(icon_path), wx.BITMAP_TYPE_ICO)
        frame.SetIcon(icon)

    frame.Show()
    app.MainLoop()
