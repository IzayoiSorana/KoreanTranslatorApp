import sys
import os
import io
import urllib.request
import threading
import re
from datetime import datetime
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout, 
                             QPushButton, QHBoxLayout, QMessageBox, QFileDialog, QScrollArea)
from PyQt6.QtCore import Qt, QRect, pyqtSignal, QObject, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QPixmap
from pynput import keyboard
import pytesseract
from deep_translator import GoogleTranslator
from PIL import Image

# Tesseract 預設安裝路徑 (Windows)
TESSERACT_CMD = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# 語言包路徑設定
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TESSDATA_DIR = os.path.join(BASE_DIR, 'tessdata')
KOR_TRAINEDDATA_URL = 'https://github.com/tesseract-ocr/tessdata/raw/main/kor.traineddata'
KOR_TRAINEDDATA_PATH = os.path.join(TESSDATA_DIR, 'kor.traineddata')
ENG_TRAINEDDATA_URL = 'https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata'
ENG_TRAINEDDATA_PATH = os.path.join(TESSDATA_DIR, 'eng.traineddata')

def ensure_tessdata():
    if not os.path.exists(TESSDATA_DIR):
        os.makedirs(TESSDATA_DIR)
    if not os.path.exists(KOR_TRAINEDDATA_PATH):
        urllib.request.urlretrieve(KOR_TRAINEDDATA_URL, KOR_TRAINEDDATA_PATH)
    if not os.path.exists(ENG_TRAINEDDATA_PATH):
        urllib.request.urlretrieve(ENG_TRAINEDDATA_URL, ENG_TRAINEDDATA_PATH)

class SignalEmitter(QObject):
    capture_triggered = pyqtSignal()
    show_message = pyqtSignal(str, str)
    show_result_image = pyqtSignal(QPixmap)
    show_error = pyqtSignal(str)

class TranslatedImageViewer(QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.pixmap = pixmap
        self.setWindowTitle("韓文翻譯合成結果")
        # 移除 WindowStaysOnTopHint 與 Tool，讓視窗有標準的縮小/放大/關閉按鈕，並且不會總是在最上層
        
        layout = QVBoxLayout()
        
        # 使用 ScrollArea 來容納可能很大的圖片
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        
        self.img_label = QLabel()
        self.img_label.setPixmap(self.pixmap)
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        scroll_area.setWidget(self.img_label)
        layout.addWidget(scroll_area)
        
        btn_layout = QHBoxLayout()
        
        self.btn_save = QPushButton("💾 儲存這張翻譯圖片")
        self.btn_save.setStyleSheet("background-color: #198754; color: white; font-weight: bold; padding: 12px; border-radius: 5px; font-size: 14px;")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.save_image)
        
        self.btn_close = QPushButton("✖ 關閉")
        self.btn_close.setStyleSheet("background-color: #6c757d; color: white; font-weight: bold; padding: 12px; border-radius: 5px; font-size: 14px;")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
        
        # 自動適應大小，但限制最大視窗尺寸
        screen_geo = QApplication.primaryScreen().geometry()
        max_w = int(screen_geo.width() * 0.8)
        max_h = int(screen_geo.height() * 0.8)
        
        target_w = min(self.pixmap.width() + 40, max_w)
        target_h = min(self.pixmap.height() + 100, max_h)
        self.resize(target_w, target_h)
        self.show()

    def save_image(self):
        default_name = f"translated_capture_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        file_path, _ = QFileDialog.getSaveFileName(self, "儲存圖片", default_name, "Images (*.png *.jpg *.bmp)")
        if file_path:
            if self.pixmap.save(file_path):
                QMessageBox.information(self, "成功", f"翻譯合成圖已成功儲存至：\n{file_path}")
            else:
                QMessageBox.warning(self, "失敗", "圖片儲存失敗，請確認路徑或權限。")

class CaptureOverlay(QWidget):
    def __init__(self, signal_emitter):
        super().__init__()
        self.signal_emitter = signal_emitter
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.begin = QPoint()
        self.end = QPoint()
        self.is_selecting = False
        self.background_pixmap = None
        
    def start_capture(self):
        screen = QApplication.primaryScreen()
        self.background_pixmap = screen.grabWindow(0)
        
        geom = QRect()
        for s in QApplication.screens():
            geom = geom.united(s.geometry())
        self.setGeometry(geom)
        self.show()
        self.activateWindow()

    def paintEvent(self, event):
        if not self.background_pixmap:
            return
            
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.background_pixmap)
        
        overlay_color = QColor(0, 0, 0, 100)
        painter.fillRect(self.rect(), overlay_color)
        
        if self.is_selecting:
            rect = QRect(self.begin, self.end).normalized()
            painter.drawPixmap(rect, self.background_pixmap.copy(rect))
            
            pen = QPen(QColor(255, 50, 50), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        # 點擊右鍵取消截圖
        if event.button() == Qt.MouseButton.RightButton:
            self.is_selecting = False
            self.hide()
            return
            
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin = event.globalPosition().toPoint()
            self.end = self.begin
            self.is_selecting = True
            self.update()

    def keyPressEvent(self, event):
        # 按下 Esc 鍵取消截圖
        if event.key() == Qt.Key.Key_Escape:
            self.is_selecting = False
            self.hide()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_selecting = False
            self.end = event.globalPosition().toPoint()
            rect = QRect(self.begin, self.end).normalized()
            self.hide() 
            
            if rect.width() > 10 and rect.height() > 10:
                threading.Thread(target=self.process_capture, args=(rect,)).start()

    def process_capture(self, rect):
        try:
            ensure_tessdata()
            if not os.path.exists(TESSERACT_CMD):
                self.signal_emitter.show_error.emit("找不到 Tesseract OCR 引擎！請確定系統已安裝 Tesseract。")
                return

            cropped = self.background_pixmap.copy(rect)
            temp_path = os.path.join(BASE_DIR, "temp_capture.png")
            cropped.save(temp_path, "PNG")
            pil_img = Image.open(temp_path)
            
            # 將圖片放大 2 倍以提升 OCR 辨識率 (避免截圖字體太小導致漏抓)
            scale_factor = 2
            w, h = pil_img.size
            pil_img_resized = pil_img.resize((w * scale_factor, h * scale_factor), Image.Resampling.LANCZOS)
            
            os.environ['TESSDATA_PREFIX'] = TESSDATA_DIR
            
            # 使用 DICT 模式取得包含座標的詳細文字資料，並同時啟用韓文與英文模型
            ocr_data = pytesseract.image_to_data(pil_img_resized, lang='kor+eng', output_type=pytesseract.Output.DICT)
            
            # 將辨識出的座標縮放回原本的尺寸
            for list_key in ['left', 'top', 'width', 'height']:
                ocr_data[list_key] = [int(val / scale_factor) for val in ocr_data[list_key]]
            
            lines = {}
            n_boxes = len(ocr_data['level'])
            for i in range(n_boxes):
                text = ocr_data['text'][i].strip()
                if not text:
                    continue
                # 改為「以段落 (Paragraph) 為單位」合併文字框
                # 這樣同一個段落內的文字就會共用一個大範圍，避免單行文字框上下互相擠壓重疊
                key = (ocr_data['block_num'][i], ocr_data['par_num'][i])
                if key not in lines:
                    lines[key] = {'text': [], 'left': [], 'top': [], 'right': [], 'bottom': []}
                
                lines[key]['text'].append(text)
                lines[key]['left'].append(ocr_data['left'][i])
                lines[key]['top'].append(ocr_data['top'][i])
                lines[key]['right'].append(ocr_data['left'][i] + ocr_data['width'][i])
                lines[key]['bottom'].append(ocr_data['top'][i] + ocr_data['height'][i])
                
            translator = GoogleTranslator(source='ko', target='zh-TW')
            
            # 先執行翻譯 (將網路請求與繪圖分離)
            translations = {}
            for key, data in lines.items():
                # 為了避免純英文單字被連在一起，我們根據字元類型決定是否加空格
                text_parts = []
                for t in data['text']:
                    t_str = t.strip()
                    if not t_str: continue
                    if text_parts and re.match(r'[A-Za-z0-9]', text_parts[-1][-1]) and re.match(r'[A-Za-z0-9]', t_str[0]):
                        text_parts.append(" " + t_str)
                    else:
                        text_parts.append(t_str)
                        
                full_text = "".join(text_parts).strip()
                if not full_text:
                    continue
                    
                # 如果整句只有英數字與一般符號 (ASCII 範圍)，就不要送翻譯，直接保留原文
                if re.match(r'^[\x00-\x7F]+$', full_text):
                    translations[key] = full_text
                    continue
                    
                # 加入重試機制，避免偶發的網路不穩或 API 阻擋
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        translations[key] = translator.translate(full_text)
                        break
                    except Exception as e:
                        if attempt == max_retries - 1:
                            translations[key] = f"[翻譯失敗]"
                            print(f"Translation error on '{full_text}': {e}")

            # ===== 開始在原圖上作畫 =====
            painter = QPainter(cropped)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    
                for key, data in lines.items():
                    translated_text = translations[key]
                    left = min(data['left'])
                    top = min(data['top'])
                    right = max(data['right'])
                    bottom = max(data['bottom'])
                    
                    # 原始韓文區域
                    box_rect = QRect(left, top, right - left, bottom - top)
                    
                    # 動態計算合適的字體大小 (根據原韓文的外框高度)
                    font_size = max(10, min(24, int((bottom - top) * 0.7)))
                    font = QFont("Microsoft JhengHei", font_size, QFont.Weight.Bold)
                    painter.setFont(font)
                    
                    # 動態縮小字體直到能塞進框框內，避免文字溢出
                    fm = painter.fontMetrics()
                    text_rect = fm.boundingRect(box_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, translated_text)
                    
                    while (text_rect.height() > box_rect.height() or text_rect.width() > box_rect.width()) and font_size > 8:
                        font_size -= 1
                        font.setPointSize(font_size)
                        painter.setFont(font)
                        fm = painter.fontMetrics()
                        text_rect = fm.boundingRect(box_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, translated_text)
                    
                    # 如果字體縮到最小還是裝不下，就擴充背景框框的範圍，確保文字不會跑到白底外面
                    bg_rect = box_rect.united(text_rect)
                    
                    # 畫上稍微半透明的白底背景 (遮蓋原本的韓文)
                    painter.fillRect(bg_rect, QColor(255, 255, 255, 245))
                    
                    # 畫極細的淺灰色邊框
                    painter.setPen(QPen(QColor(200, 200, 200), 1))
                    painter.drawRect(bg_rect)
                    
                    painter.setPen(QColor(0, 0, 0)) # 黑字
                    
                    # 畫上翻譯文字 (自動換行置中對齊)
                    painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, translated_text)
            finally:
                painter.end()
            # ==============================
            
            if lines:
                # 傳遞已經合成完畢的圖片到主執行緒
                self.signal_emitter.show_result_image.emit(cropped)
            else:
                self.signal_emitter.show_error.emit("未能辨識出文字")
        except Exception as e:
            self.signal_emitter.show_error.emit(f"錯誤發生:\n{str(e)}")

class TranslatorApp(QObject):
    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        self.signal_emitter = SignalEmitter()
        self.viewers = [] # 儲存所有的檢視視窗，避免被記憶體回收
        
        self.capture_overlay = CaptureOverlay(self.signal_emitter)
        
        self.signal_emitter.capture_triggered.connect(self.capture_overlay.start_capture)
        self.signal_emitter.show_message.connect(self.show_msg)
        self.signal_emitter.show_result_image.connect(self.display_image_viewer)
        self.signal_emitter.show_error.connect(self.display_error)
        
        self.setup_hotkey()
        
    def setup_hotkey(self):
        def on_activate():
            self.signal_emitter.capture_triggered.emit()

        hotkey_str = '<cmd>+<shift>+q'
        self.listener = keyboard.GlobalHotKeys({
            hotkey_str: on_activate
        })
        self.listener.start()
        
    def display_image_viewer(self, pixmap):
        viewer = TranslatedImageViewer(pixmap)
        self.viewers.append(viewer)
        
        # 清理已經關閉的視窗
        self.viewers = [v for v in self.viewers if v.isVisible()]

    def display_error(self, error_msg):
        self.show_msg("提示", error_msg)

    def show_msg(self, title, content):
        msg = QMessageBox()
        msg.setWindowTitle(title)
        msg.setText(content)
        msg.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        msg.exec()
        
    def run(self):
        sys.exit(self.app.exec())

if __name__ == '__main__':
    app = TranslatorApp()
    app.run()
