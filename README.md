## 安裝GUI套件及打包套件

### PyQt
```bash
pip install PyQt6
```

### wxPython
```bash
pip install wxPython
```

### PyInstaller
```bash
pip install pyinstaller
```

## 打包程式為執行檔

### PyQt

#### 打包成單一獨立的 .exe 檔案
```Bash
pyinstaller --noconsole --onefile --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_fw_tool.py
```

### wxPython

#### 打包為包含主程式與 DLL 的資料夾（啟動較快、底層庫相容性高）
```bash
pyinstaller --noconfirm --onedir --windowed --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_rw_tool_wx.py
```

#### 打包成單一獨立的 .exe 檔案
```bash
pyinstaller --noconfirm --onefile --windowed --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_rw_tool_wx.py
```

#### 打包時嵌入管理員權限要求
```bash
pyinstaller --noconfirm --onefile --windowed --uac-admin --icon=usb_hid_tool.ico --add-data "usb_hid_tool.ico;." usb_hid_rw_tool_wx.py
```
