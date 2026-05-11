# 🇰🇷 韓文截圖翻譯 APP V2 實作計畫

為了徹底解決 V1 版本中 Tesseract OCR 帶來的「抓錯範圍」、「漏字」以及「排版重疊」等問題，我們將重新打造 V2 版本。核心重點是將辨識引擎更換為地表最強開源字元辨識套件 **EasyOCR**，並重新設計更乾淨、模組化的程式架構。

## User Review Required

> [!WARNING]
> **關於 EasyOCR 的安裝時間**
> EasyOCR 底層使用的是 PyTorch（強大的深度學習框架），因此**安裝檔會比較大（約 2GB）**。初次執行安裝腳本時可能需要等待 3~5 分鐘下載。但換來的好處是：我們將獲得極度精準的遊戲截圖文字定位與辨識能力，不再需要手動放大圖片或拼湊段落。請問您能接受較大的安裝包嗎？

## Proposed Changes

我們將在同一個資料夾內建立全新的 V2 相關檔案，讓您隨時可以與 V1 做比較。

### 核心程式重構 (Python)

#### [NEW] [v2_main.py](file:///C:/Users/doctor/.gemini/antigravity/scratch/KoreanTranslatorApp/v2_main.py)
全新的主程式，架構將拆分為四個獨立明確的模組：
1. **`ScreenCapturer`**: 專職處理 `Win+Shift+Q` 快捷鍵監聽、螢幕變暗與滑鼠框選邏輯。
2. **`OCREngine`**: 在背景載入 `easyocr.Reader(['ko', 'en'])`。它會回傳精準的四角座標與高信心度的文字。
3. **`Translator`**: 專職負責過濾純英文（保留原味）以及透過 Google 翻譯轉換韓文，並具備智慧重試機制。
4. **`ImageRenderer`**: 負責最終圖片的繪製。得益於 EasyOCR 的精準座標，我們可以直接在準確的位置上蓋上白底黑字，確保邊距完美貼合且不重疊。

### 安裝與啟動腳本

#### [NEW] [v2_install.bat](file:///C:/Users/doctor/.gemini/antigravity/scratch/KoreanTranslatorApp/v2_install.bat)
- 移除不再需要的 Tesseract 與 pytesseract。
- 新增 `easyocr` 與其依賴的 `torch` 下載邏輯。

#### [NEW] [v2_run.bat](file:///C:/Users/doctor/.gemini/antigravity/scratch/KoreanTranslatorApp/v2_run.bat)
- 用於啟動全新的 `v2_main.py`。

---

## Verification Plan

1. **環境建置測試**：執行 `v2_install.bat`，確保 PyTorch 與 EasyOCR 能在您的 Windows 環境下順利安裝。
2. **啟動測試**：執行 `v2_run.bat`，確認第一次載入 AI 模型（EasyOCR 初次啟動會下載約 20MB 的模型檔）是否成功。
3. **極限翻譯測試**：請您用 `Win+Shift+Q` 框選之前那些「密集重疊的韓文」以及「帶有英文的遊戲專有名詞」，驗證 EasyOCR 的精確定位是否徹底解決了覆蓋與亂碼問題。
