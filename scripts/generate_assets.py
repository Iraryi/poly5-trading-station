from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from poly5.terminal_widgets import write_icon,brand_pixmap
app=QApplication([])
destination=Path(__file__).resolve().parents[1]/'assets'
destination.mkdir(exist_ok=True)
write_icon(destination/'poly5.ico')
brand_pixmap(256).save(str(destination/'poly5.png'))
