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
        bpl = qimg.bytesPerLine()
        
        ptr = qimg.bits()
        ptr.setsize(height * bpl)
        arr = np.frombuffer(ptr, np.uint8).reshape((height, bpl))
        # 移除 Qt 用來對齊記憶體的 Padding 像素
        arr = arr[:, :width * 3].reshape((height, width, 3))
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    def cv_to_qpixmap(self, cv_img):
        # 轉換為 RGB 並強制複製 (copy) 確保記憶體連續，避免 PyQt 讀取時花屏
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB).copy()
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qimg = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        return QPixmap.fromImage(qimg)

    def get_luminance(self, r, g, b):
        return (r * 299 + g * 587 + b * 114) / 1000

    def snap_color(self, r, g, b, is_text=False):
        lum = self.get_luminance(r, g, b)
        if not is_text: # 步驟 1：背景判定 (大於 200 變白，小於 50 變黑)
            if lum > 200: return (255, 255, 255)
            if lum < 50: return (0, 0, 0)
            return (r, g, b)
        else: # 步驟 2：文字對齊 (靠近白色轉白，靠近黑色轉黑)
            if lum > 180: return (255, 255, 255)
            if lum < 80: return (0, 0, 0)
            return (r, g, b)

    def get_luminance(self, r, g, b):
        return (r * 299 + g * 587 + b * 114) / 1000

    def snap_color(self, r, g, b, is_text=False):
        lum = self.get_luminance(r, g, b)
        if not is_text: # 背景判定
            rgb_diff = max(r, g, b) - min(r, g, b)
            
            # 1. 亮度 > 235 直接給純白
            # 2. 飽和度檢查：如果是淺灰色 (R,G,B 差值 < 20 且 亮度 > 210)，大膽轉純白
            if lum > 235 or (lum > 210 and rgb_diff < 20): 
                return (255, 255, 255)
                
            if lum < 50: return (0, 0, 0)
            return (r, g, b)
        else: # 文字對齊 (靠近白色轉白，靠近黑色轉黑)
            if lum > 180: return (255, 255, 255)
            if lum < 80: return (0, 0, 0)
            return (r, g, b)

    def process(self, pixmap, ocr_data):
        print(f"🎨 [模組 D] 啟動影像合成，使用全圖色彩分群與進階羽化模式...")
        cv_img = self.qpixmap_to_cv(pixmap)
        
        # --- 第一階段：收集所有框的預設背景色 ---
        all_task_info = []
        for item in ocr_data:
            bbox = item['bbox']
            x_coords = [p[0] for p in bbox]
            y_coords = [p[1] for p in bbox]
            x = int(min(x_coords))
            y = int(min(y_coords))
            w = int(max(x_coords) - x)
            h = int(max(y_coords) - y)
            text = item['translated_text']
            
            roi_y1 = max(0, y - 2)
            roi_y2 = min(cv_img.shape[0], y + h + 2)
            roi_x1 = max(0, x - 2)
            roi_x2 = min(cv_img.shape[1], x + w + 2)
            roi = cv_img[roi_y1:roi_y2, roi_x1:roi_x2]
            
            if roi.size == 0: continue
            
            # 量化顏色
            quantized = (roi // 32) * 32
            pixels = quantized.reshape(-1, 3)
            unique, counts = np.unique(pixels, axis=0, return_counts=True)
            sorted_indices = np.argsort(-counts)
            
            bg_bgr = unique[sorted_indices[0]]
            bg_r, bg_g, bg_b = int(bg_bgr[2]), int(bg_bgr[1]), int(bg_bgr[0])
            raw_bg = self.snap_color(bg_r, bg_g, bg_b, is_text=False)
            
            all_task_info.append({
                "x": x, "y": y, "w": w, "h": h,
                "roi_x1": roi_x1, "roi_y1": roi_y1, "roi_x2": roi_x2, "roi_y2": roi_y2,
                "text": text,
                "raw_bg": raw_bg,
                "unique_colors": unique,
                "sorted_indices": sorted_indices
            })
            
        # --- 第二階段：全圖色彩分群 (Global Color Grouping) ---
        clusters = []
        for task in all_task_info:
            r, g, b = task['raw_bg']
            found_cluster = False
            for cluster in clusters:
                cr, cg, cb = cluster['avg']
                # 歐幾里得距離判斷顏色相似度，相近的顏色歸為同一群
                dist = ((r-cr)**2 + (g-cg)**2 + (b-cb)**2) ** 0.5
                if dist < 30: 
                    cluster['sum_r'] += r
                    cluster['sum_g'] += g
                    cluster['sum_b'] += b
                    cluster['count'] += 1
                    cluster['avg'] = (
                        int(cluster['sum_r']/cluster['count']),
                        int(cluster['sum_g']/cluster['count']),
                        int(cluster['sum_b']/cluster['count'])
                    )
                    task['cluster'] = cluster
                    found_cluster = True
                    break
                    
            if not found_cluster:
                new_cluster = {"sum_r": r, "sum_g": g, "sum_b": b, "count": 1, "avg": (r, g, b)}
                clusters.append(new_cluster)
                task['cluster'] = new_cluster
                
        # --- 第三階段：套用群組色、羽化渲染與尋找文字色 ---
        text_tasks = []
        for task in all_task_info:
            # 取得該群組的統一平均背景色
            final_bg = task['cluster']['avg']
            bg_lum = self.get_luminance(*final_bg)
            
            # 從原本的顏色分佈中，尋找對比度足夠的顏色作為文字色
            unique = task['unique_colors']
            sorted_indices = task['sorted_indices']
            text_found = False
            for i in range(1, len(sorted_indices)):
                c_bgr = unique[sorted_indices[i]]
                c_r, c_g, c_b = int(c_bgr[2]), int(c_bgr[1]), int(c_bgr[0])
                c_lum = self.get_luminance(c_r, c_g, c_b)
                if abs(c_lum - bg_lum) > 40: 
                    text_r, text_g, text_b = c_r, c_g, c_b
                    text_found = True
                    break
                    
            if not text_found:
                text_r, text_g, text_b = (0,0,0) if bg_lum > 127 else (255,255,255)
                
            final_text = self.snap_color(text_r, text_g, text_b, is_text=True)
            text_lum = self.get_luminance(*final_text)
            
            if abs(bg_lum - text_lum) < 50:
                final_text = (0, 0, 0) if bg_lum > 127 else (255, 255, 255)
                
            # 繪製漸層羽化遮罩 (Alpha Blur)
            # 建立全圖大小的黑色遮罩
            mask = np.zeros(cv_img.shape[:2], dtype=np.float32)
            # 畫白色矩形前，先向外擴充幾像素，確保模糊後中心還是 100% 不透明
            pad = 4
            cv2.rectangle(mask, 
                         (max(0, task['roi_x1'] - pad), max(0, task['roi_y1'] - pad)), 
                         (min(cv_img.shape[1], task['roi_x2'] + pad), min(cv_img.shape[0], task['roi_y2'] + pad)), 
                         1.0, -1)
            # 使用非常大的 Kernel (21x21) 做出極為柔和的邊緣漸層過渡
            mask = cv2.GaussianBlur(mask, (21, 21), 0)
            
            color_bgr = (final_bg[2], final_bg[1], final_bg[0])
            solid_bg = np.full_like(cv_img, color_bgr)
            
            # 影像 alpha 混和
            mask_3d = mask[:, :, np.newaxis]
            cv_img[:] = cv_img * (1 - mask_3d) + solid_bg * mask_3d
            
            text_tasks.append({
                "rect": QRectF(task['x'], task['y'], task['w'], task['h']),
                "text": task['text'],
                "color": QColor(*final_text)
            })

        # --- 第四階段：將影像轉回 PyQt 進行高畫質文字渲染 ---
        result_pixmap = self.cv_to_qpixmap(cv_img)
        result_pixmap.setDevicePixelRatio(1.0)
        
        painter = QPainter(result_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        font_size = 18
        font = QFont("Microsoft JhengHei", font_size, QFont.Weight.Bold)
        
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        for task in text_tasks:
            rect = task["rect"]
            text = task["text"]
            
            current_font_size = font_size
            font.setPointSize(current_font_size)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            text_rect = metrics.boundingRect(QRect(0, 0, int(rect.width()), int(rect.height())), Qt.TextFlag.TextWordWrap, text)
            
            while text_rect.height() > rect.height() and current_font_size > 8:
                current_font_size -= 1
                font.setPointSize(current_font_size)
                painter.setFont(font)
                metrics = painter.fontMetrics()
                text_rect = metrics.boundingRect(QRect(0, 0, int(rect.width()), int(rect.height())), Qt.TextFlag.TextWordWrap, text)
            
            painter.setPen(QPen(task["color"]))
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
