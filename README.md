## 安裝GUI套件及打包套件

```bash
# PyQt
pip install PyQt6
# wxPython
pip install wxPython
# PyInstaller
pip install pyinstaller
```

## 打包程式為執行檔

### PyQt

```Bash
# 打包成單一獨立的 .exe 檔案
pyinstaller --noconsole --onefile --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_fw_tool.py
```

### wxPython

```bash
# 打包為包含主程式與 DLL 的資料夾（啟動較快、底層庫相容性高）
pyinstaller --noconfirm --onedir --windowed --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_rw_tool_wx.py
# 打包成單一獨立的 .exe 檔案
pyinstaller --noconfirm --onefile --windowed --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_rw_tool_wx.py
# 打包時嵌入管理員權限要求
pyinstaller --noconfirm --onefile --windowed --uac-admin --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_rw_tool_wx.py
```
