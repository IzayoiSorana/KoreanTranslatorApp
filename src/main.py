import sys
import os
import re
import threading
from PyQt6.QtWidgets import QApplication, QWidget, QScrollArea, QVBoxLayout, QLabel
from PyQt6.QtCore import pyqtSignal, QObject, Qt, QRect, QRectF, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap, QImage, QFont, QTextOption
import cv2
import numpy as np
from pynput import keyboard
import easyocr
from deep_translator import GoogleTranslator

# 建立一個訊號發送器，用來讓背景快捷鍵安全地通知前景 UI
class SignalEmitter(QObject):
    capture_triggered = pyqtSignal()
    capture_finished = pyqtSignal(QPixmap)  # 傳遞截圖完成的圖片
    show_debug_image = pyqtSignal(QPixmap)  # [新增] 傳遞繪製好紅色邊框的除錯圖片

# 🧩 模組 B：字元辨識模組
class OCREngine:
    def __init__(self):
        print("⏳ [模組 B] 正在載入 EasyOCR AI 模型... (初次載入需等候幾秒)")
        self.reader = easyocr.Reader(['ko', 'en'])
        print("✅ [模組 B] 模型載入完成！")
        
    def process(self, pixmap):
        # 為了穩定，將 QPixmap 存成暫存檔交給 EasyOCR
        temp_path = "temp_v2_capture.png"
        pixmap.save(temp_path, "PNG")
        
        # 執行辨識，detail=1 會回傳 (bbox, text, prob)
        results = self.reader.readtext(temp_path)
        
        output = []
        for bbox, text, prob in results:
            clean_text = text.strip()
            if clean_text:
                output.append({
                    "bbox": bbox,
                    "text": clean_text,
                    "confidence": prob
                })
        return output

# 🧩 模組 C：翻譯引擎介面 (Strategy Pattern)
class BaseTranslationEngine:
    def translate(self, text: str, source: str, target: str) -> str:
        raise NotImplementedError("必須由子類別實作此方法")

class GoogleEngine(BaseTranslationEngine):
    def __init__(self):
        self.translator = GoogleTranslator()
        
    def translate(self, text: str, source: str, target: str) -> str:
        self.translator.source = source
        self.translator.target = target
        return self.translator.translate(text)

# 🧩 模組 C：翻譯與防護管線
class Translator:
    def __init__(self, engine: BaseTranslationEngine, source_lang='ko', target_lang='zh-TW'):
        self.engine = engine
        self.source = source_lang
        self.target = target_lang
        
    def process(self, ocr_data):
        print(f"🌍 [模組 C] 準備單行獨立翻譯 {len(ocr_data)} 句文字...")
        for item in ocr_data:
            text = item["text"]
            # 防護機制：純英數字/一般符號保留原味
            if re.match(r'^[\x00-\x7F]+$', text):
                item["translated_text"] = text
                continue
                
            # 呼叫動態掛載的翻譯引擎
            try:
                translated = self.engine.translate(text, self.source, self.target)
                item["translated_text"] = translated if translated else text
            except Exception as e:
                print(f"⚠️ 翻譯失敗 ({text}): {e}")
                item["translated_text"] = text # 失敗則保留原文
                
        return ocr_data

# 🧩 模組 D：影像合成模組
class ImageRenderer:
    def __init__(self, mode="inpaint"):
        # mode 可以是 "inpaint" (魔法修補法) 或 "stroke" (文字描邊法)
        self.mode = mode
        
    def qpixmap_to_cv(self, pixmap):
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
        width = qimg.width()
        height = qimg.height()
        ptr = qimg.bits()
        ptr.setsize(height * width * 3)
        arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 3))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    def cv_to_qpixmap(self, cv_img):
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qimg = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    def process(self, pixmap, ocr_data):
        print(f"🎨 [模組 D] 啟動影像合成，使用模式: {self.mode}")
        result_pixmap = pixmap.copy()
        
        if self.mode == "inpaint":
            print("✨ 執行 OpenCV 魔法修補背景...")
            cv_img = self.qpixmap_to_cv(result_pixmap)
            # 建立黑色遮罩
            mask = np.zeros(cv_img.shape[:2], dtype=np.uint8)
            for item in ocr_data:
                bbox = item['bbox']
                pts = np.array([[int(p[0]), int(p[1])] for p in bbox], np.int32)
                # 在遮罩上畫出白色的要修補的區域
                cv2.fillPoly(mask, [pts], 255)
            
            # 使用 INPAINT_TELEA 演算法進行周圍像素填補
            inpaint_radius = 5
            cv_img = cv2.inpaint(cv_img, mask, inpaint_radius, cv2.INPAINT_TELEA)
            result_pixmap = self.cv_to_qpixmap(cv_img)
            
        # 修正 HiDPI 螢幕縮放問題 (強制像素 1:1，避免畫錯位置)
        result_pixmap.setDevicePixelRatio(1.0)
        # 開始畫文字
        painter = QPainter(result_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # 設定字型
        font = QFont("Microsoft JhengHei", 12, QFont.Weight.Bold)
        painter.setFont(font)
        
        # 設定自動換行與置中
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        # 為了取樣背景顏色，預先轉換一張 Image
        img_for_sampling = result_pixmap.toImage()
        
        for item in ocr_data:
            bbox = item['bbox']
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]
            x = float(min(x_coords))
            y = float(min(y_coords))
            w = float(max(x_coords) - x)
            h = float(max(y_coords) - y)
            rect = QRectF(x, y, w, h)
            text = item['translated_text']
            
            # --- 1. 背景與文字顏色自動適配 ---
            # 採樣背景色 (取框正上方 2px)
            bg_sample_x = int(max(0, x))
            bg_sample_y = int(max(0, y - 2)) 
            if bg_sample_x < img_for_sampling.width() and bg_sample_y < img_for_sampling.height():
                bg_color = img_for_sampling.pixelColor(bg_sample_x, bg_sample_y)
            else:
                bg_color = QColor(255, 255, 255)
                
            # 採樣文字色 (取框正中央)
            text_sample_x = int(x + w/2)
            text_sample_y = int(y + h/2)
            if text_sample_x < img_for_sampling.width() and text_sample_y < img_for_sampling.height():
                text_color = img_for_sampling.pixelColor(text_sample_x, text_sample_y)
            else:
                text_color = QColor(0, 0, 0)
                
            # 防呆：確保對比度足夠，否則強制用黑或白
            bg_lum = (bg_color.red() * 299 + bg_color.green() * 587 + bg_color.blue() * 114) / 1000
            text_lum = (text_color.red() * 299 + text_color.green() * 587 + text_color.blue() * 114) / 1000
            if abs(bg_lum - text_lum) < 60:
                text_color = QColor(0, 0, 0) if bg_lum > 127 else QColor(255, 255, 255)
            
            # 畫出背景填補 (抹平原文字)
            bg_rect = rect.adjusted(-2, -2, 2, 2)
            painter.setBrush(bg_color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(bg_rect)
            
            # --- 2. 字體大小動態調整 (最佳縮放比例) ---
            font_size = 18 # 初始最大字體
            font = QFont("Microsoft JhengHei", font_size, QFont.Weight.Bold)
            painter.setFont(font)
            
            # 不斷縮小字體直到塞得進框框高度
            metrics = painter.fontMetrics()
            text_rect = metrics.boundingRect(QRect(0, 0, int(w), int(h)), Qt.TextFlag.TextWordWrap, text)
            while text_rect.height() > h and font_size > 8:
                font_size -= 1
                font.setPointSize(font_size)
                painter.setFont(font)
                metrics = painter.fontMetrics()
                text_rect = metrics.boundingRect(QRect(0, 0, int(w), int(h)), Qt.TextFlag.TextWordWrap, text)
            
            # --- 3. 畫上文字 ---
            painter.setPen(QPen(text_color))
            painter.drawText(rect, text, option)
            
        painter.end()
        return result_pixmap

# 🧩 模組 E：檢視與儲存模組
class ResultViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("翻譯結果預覽")
        self.resize(800, 600)
        # 設定視窗在最上層，方便使用者查看
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        
        layout = QVBoxLayout(self)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area)
        
    def show_image(self, pixmap):
        self.image_label.setPixmap(pixmap)
        self.show()
        self.activateWindow()

class CaptureOverlay(QWidget):
    def __init__(self, signal_emitter):
        super().__init__()
        self.signal_emitter = signal_emitter
        # 設定為無視窗邊框、強制最上層、不在工作列顯示的工具視窗
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.Tool
        )
        # 設定滑鼠游標為十字準心
        self.setCursor(Qt.CursorShape.CrossCursor)
        
        self.begin = QPoint()
        self.end = QPoint()
        self.is_selecting = False
        self.frozen_pixmap = None

    def start_capture(self):
        print("📸 啟動截圖遮罩 (畫面已凍結)...")
        # 1. 支援多螢幕：計算所有螢幕的總範圍
        geom = QRect()
        for screen in QApplication.screens():
            geom = geom.united(screen.geometry())
        
        # 2. 凍結畫面：抓取涵蓋所有螢幕範圍的全桌面截圖
        screen = QApplication.primaryScreen()
        self.frozen_pixmap = screen.grabWindow(0, geom.x(), geom.y(), geom.width(), geom.height())
        
        self.setGeometry(geom)
        self.show()
        self.activateWindow()

    def paintEvent(self, event):
        if not self.frozen_pixmap:
            return
            
        painter = QPainter(self)
        # 先畫上原本凍結的清晰畫面
        painter.drawPixmap(self.rect(), self.frozen_pixmap)
        
        # 鋪上一層半透明的黑布，讓畫面變暗
        overlay_color = QColor(0, 0, 0, 150)
        painter.fillRect(self.rect(), overlay_color)
        
        # 如果正在框選，就把框選範圍內的黑布「挖洞」(重畫一次原本清晰的畫面)
        if self.is_selecting:
            rect = QRect(self.begin, self.end).normalized()
            painter.drawPixmap(rect, self.frozen_pixmap.copy(rect))
            
            # 畫上明顯的紅色外框
            pen = QPen(QColor(255, 50, 50), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        # 點擊右鍵取消截圖
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel_capture()
            return
            
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin = event.globalPosition().toPoint()
            self.end = self.begin
            self.is_selecting = True
            self.update()

    def keyPressEvent(self, event):
        # 按下 Esc 鍵取消截圖
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_capture()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end = event.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_selecting:
            self.is_selecting = False
            self.end = event.globalPosition().toPoint()
            rect = QRect(self.begin, self.end).normalized()
            self.hide() 
            
            if rect.width() > 10 and rect.height() > 10:
                print(f"✅ 成功選取範圍: 座標 {rect.x()},{rect.y()} 寬 {rect.width()} 高 {rect.height()}")
                # 裁切出使用者選取的圖片部分
                cropped_img = self.frozen_pixmap.copy(rect)
                # 發送訊號，把圖片交給主程式
                self.signal_emitter.capture_finished.emit(cropped_img)
            else:
                print("⚠️ 選取範圍太小，已忽略")

    def cancel_capture(self):
        self.is_selecting = False
        self.hide()
        print("❌ 已取消截圖")

class TranslatorAppV2(QObject):
    def __init__(self):
        super().__init__()
        # 1. 初始化 PyQt 應用程式
        self.app = QApplication(sys.argv)
        # 讓程式在沒有視窗開啟時也能繼續在背景執行
        self.app.setQuitOnLastWindowClosed(False)
        
        self.signal_emitter = SignalEmitter()
        self.capture_overlay = CaptureOverlay(self.signal_emitter)
        self.result_viewer = ResultViewer()
        
        # 將快捷鍵的訊號，連接到截圖遮罩的啟動函數
        self.signal_emitter.capture_triggered.connect(self.capture_overlay.start_capture)
        # 將截圖完成的訊號，連接到處理管線
        self.signal_emitter.capture_finished.connect(self.on_capture_finished)
        # 將除錯圖片訊號，連接到檢視器
        self.signal_emitter.show_debug_image.connect(self.result_viewer.show_image)
        
        # 2. 啟動背景快捷鍵監聽
        self.setup_hotkey()
        print("🚀 韓文截圖翻譯 APP (V2) 介面啟動！")
        
        # 初始化模組 B 與 C (會稍微花一點時間載入模型)
        self.ocr_engine = OCREngine()
        # 動態組裝翻譯模組，未來只需抽換 GoogleEngine() 即可
        translation_engine = GoogleEngine()
        self.translator = Translator(engine=translation_engine, source_lang='ko', target_lang='zh-TW')
        
        print("👀 準備就緒！正在背景監聽快捷鍵：Win + Shift + Q ...")

    def on_capture_finished(self, pixmap):
        # 為了避免 OCR 和翻譯卡住視窗，我們開一個獨立執行緒來跑這段「資料流管線」
        threading.Thread(target=self._run_pipeline, args=(pixmap,), daemon=True).start()
        
    def _run_pipeline(self, pixmap):
        print("🔍 收到截圖！啟動模組 B (EasyOCR) 檢測文字...")
        ocr_result = self.ocr_engine.process(pixmap)
        
        if not ocr_result:
            print("⚠️ 找不到任何文字，流程結束。")
            return
            
        print("🌐 檢測完畢，啟動模組 C (Google Translate)...")
        final_data = self.translator.process(ocr_result)
        
        print("✅ 翻譯完成！(內部除錯結果如下)：")
        for item in final_data:
            print(f"   [{item['text']}] -> [{item['translated_text']}]")
            
        print("🎨 準備進入模組 D (影像合成)...")
        # 💡 您可以在這裡自由切換模式："inpaint" (魔法修補法) 或 "stroke" (文字描邊法)
        renderer = ImageRenderer(mode="stroke")
        final_pixmap = renderer.process(pixmap, final_data)
        
        # 透過訊號將最終合成的圖片傳給主執行緒的 UI 顯示出來
        self.signal_emitter.show_debug_image.emit(final_pixmap)
        
    def setup_hotkey(self):
        def on_activate():
            # 因為 pynput 在獨立執行緒運作，所以我們發出訊號讓主程式處理後續動作
            self.signal_emitter.capture_triggered.emit()

        # 在 pynput 中，Windows 鍵被稱為 <cmd>
        hotkey_str = '<cmd>+<shift>+q'
        self.listener = keyboard.GlobalHotKeys({
            hotkey_str: on_activate
        })
        # 啟動監聽執行緒
        self.listener.start()



    def run(self):
        # 進入 PyQt 主迴圈，保持程式運行
        sys.exit(self.app.exec())

if __name__ == '__main__':
    app = TranslatorAppV2()
    app.run()
