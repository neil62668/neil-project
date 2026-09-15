import sys
from typing import Any, Dict, List, Optional, Union

# 定義傳入寫入/Feature Report 資料的型別 (支援 bytes, bytearray 或整數列表)
BufferType = Union[bytes, bytearray, List[int]]

# 錯誤類別 (hidapi 通常拋出 OSError 或 ValueError，自訂例外供參考)
class HIDException(Exception): ...

def enumerate(vendor_id: int = 0, product_id: int = 0) -> List[Dict[str, Any]]:
    """列出系統中符合條件的 USB/Bluetooth HID 設備資訊字典清單。"""
    ...

class device:
    def __init__(self) -> None: ...
    def open(self, vendor_id: int, product_id: int, serial_number: Optional[str] = None) -> None:
        """透過 VID/PID (及可選的序列號) 開啟 HID 設備。"""
        ...

    def open_path(self, path: bytes) -> None:
        """透過系統設備路徑開啟 HID 設備 (通常由 enumerate 取得)。"""
        ...

    def close(self) -> None:
        """關閉已開啟的 HID 設備。"""
        ...

    def write(self, data: BufferType) -> int:
        """
        向設備寫入 Output Report。
        第一個 Byte 必須是 Report ID (無 Report ID 則傳 0x00)。
        傳回實際寫入的 Byte 數量。
        """
        ...

    def read(self, max_length: int, timeout_ms: int = -1) -> List[int]:
        """
        自設備讀取 Input Report。
        timeout_ms: 讀取逾時時間 (毫秒)，設為 -1 表示阻塞等待。
        傳回整數 Byte 列表 (List[int])。
        """
        ...

    def set_nonblocking(self, nonblock: int) -> int:
        """
        設定讀取模式為非阻塞 (1) 或阻塞 (0)。
        傳回 0 代表成功，-1 代表失敗。
        """
        ...

    def send_feature_report(self, data: BufferType) -> int:
        """
        向設備發送 Feature Report。
        第一個 Byte 必須是 Feature Report ID。
        傳回實際發送的 Byte 數量，失敗時拋出 OSError。
        """
        ...

    def get_feature_report(self, report_id: int, max_length: int) -> List[int]:
        """
        自設備要求指定 Report ID 的 Feature Report。
        第一個元素為傳回的 Report ID。
        傳回整數 Byte 列表 (List[int])。
        """
        ...

    def error(self) -> str:
        """取得最後一次操作失敗的系統錯誤描述字串。"""
        ...

    def get_manufacturer_string(self) -> str:
        """取得製造商名稱。"""
        ...

    def get_product_string(self) -> str:
        """取得產品名稱。"""
        ...

    def get_serial_number_string(self) -> str:
        """取得設備序列號。"""
        ...

    def get_indexed_string(self, string_index: int) -> str:
        """依據 Descriptor Index 取得特定字串。"""
        ...
    if sys.version_info >= (3, 8):
        def __enter__(self) -> device: ...
        def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None: ...
