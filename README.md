# Forge Character Builder

[Forge Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo)（`neo` 分支）用的角色組件 Prompt Builder 與 Pose 傳送器：
以 [CharaDB](https://github.com/taki-ttt/charadb) 的 Danbooru tags 組合髮型／眼睛／服裝，
並把 [ai-pose-gallery-zh](https://github.com/anhao/ai-pose-gallery-zh) 的姿勢圖送到 ControlNet。

> 本專案原名 `ai-pose-gallery-forge`，與 AIポーズギャラリー 及 ai-pose-gallery-zh 的作者無關聯。

## 功能

- 中文搜尋與分類篩選
- 48 張一頁的延遲式素材瀏覽，避免一次載入上萬張圖片
- 姿勢原圖／Depth 圖切換
- 安全模式（預設隱藏 18+ 分類）
- 送到 `txt2img + ControlNet 1`
- 送到 `img2img`
- 將素材提示詞送到 `txt2img`
- 服裝關鍵字依衣物詞組插入逗號，保留 `mini skirt`、`white sneakers` 等多字詞組

## 安裝

建議將原始資料庫放在本擴充的 `vendor` 目錄，避免 Forge 把原始專案的
資料維護腳本誤認成 WebUI 擴充腳本：

```text
extensions/
└─ forge-character-builder/
   ├─ scripts/
   └─ vendor/
      ├─ data/
      ├─ img/
      └─ thumbs/
```

也可以用環境變數 `ANHAO_GALLERY_ROOT` 指定原始資料庫路徑（舊名 `AI_POSE_GALLERY_ROOT` 仍可使用）。

重新啟動 Forge 後，頂部會出現「Character Builder」分頁。

從舊名稱升級：把 `extensions/ai-pose-gallery-forge` 資料夾改名為
`extensions/forge-character-builder`（或重新 clone），`vendor/` 內容不用變動。

### CharaDB（選用，Character Builder 用）

Character Builder 的髮型／眼睛／服裝 tags 來自
[CharaDB](https://github.com/taki-ttt/charadb)。資料與圖片都需自行下載，
本擴充不會下載或重新散布任何圖片。建議做法：把官方 Release zip 解壓縮到
`vendor/charadb/`，結構如下：

```text
vendor/charadb/
  ├─ data.js          ← Release 內附的索引（或改放 data/items_public.json）
  ├─ thumb/{id}.webp  ← Gallery 縮圖
  ├─ img/{id}.jpg
  └─ line/{id}.png
```

- 索引檔依序尋找 `data/items_public.json`、`items_public.json`、`data.js`；
  有 `items_public.json` 時優先使用（保留 `color` 欄位）。
- 也可用環境變數 `CHARADB_ROOT` 指定上述資料夾（含 `thumb/` 的那一層）。
- 只放索引、沒有圖片時，Builder 仍可運作，縮圖顯示 no-preview。

未安裝 CharaDB 時，Builder 的 Outfit 可改用 Anhao 服裝，既有的 Pose Gallery
分頁照常可用。

### 設定與資料來源狀態

在 **Settings → Forge Character Builder** 可調整（變更後需按 Reload UI）：

- **Show the Portraits library in the Pose Gallery tab**：預設關閉。
- **Offer Anhao (Original) outfits as a Character Builder outfit source**：預設開啟。

Character Builder 分頁上方的 **Data sources** 會顯示各來源的數量與狀態
（✅ 正常、⚠️ 部分可用、⛔ 未安裝、❌ 檔案無法讀取），有問題時自動展開，
並列出應放置的路徑或環境變數。

### 使用方式

Character Builder 分頁有三個步驟：

1. **Character**：選髮型（Gallery）、髮色、瀏海、髮飾、眼睛顏色與眼型（Dropdown）。
2. **Outfit**：從 CharaDB 或 Anhao 選服裝。
3. **Pose**：Anhao 同一姿勢的各視角合併成一張卡片；點選後可在 **View angle** 切換 Front／High angle／Low angle／Side／Three-quarter（只顯示確實存在的角度）。Normal 送 `canny`、Depth 送 `None` 預處理器。

Pose Gallery（Legacy）也支援同一姿勢多視角切換，原有服裝與 Portraits 圖庫不受影響。姿勢以「體系 + 姿勢編號」分組；本機 Anhao 資料庫共有 1,439 筆視角紀錄、288 組獨立姿勢（其中 287 組有五視角、1 組有四視角）。

右側的 Final Prompt 可直接修改。**Append** 只在 txt2img Prompt 末尾追加，
不更動既有的 Prompt、LoRA 與 Negative Prompt；**Send All** 先把 Pose 送到
ControlNet Unit 1，成功後才追加 Prompt。Safe mode 會隱藏 18+ 素材，並清除已選的
18+ 項目。

## ControlNet 建議

- 普通姿勢圖：擴充會預選 `canny`，模型請選 Canny ControlNet（例如 xinsirControlnetCanny_v20）。
- Depth 圖：擴充會預選 `None`，模型請選 Depth ControlNet。
- 服裝與人物圖：適合送到 img2img，或自行搭配 Reference / IP-Adapter。

## 開發與測試

共用邏輯位於擴充根目錄的 `character_builder_core/`（純 Python，不依賴 Gradio／Forge）。
在專案根目錄執行：

```bash
python -m unittest discover -s tests
```

若要額外對真實 CharaDB 索引驗證，設定 `CHARADB_ITEMS=<items_public.json 路徑>` 後再執行。

## 授權

本擴充程式碼可獨立授權；原始專案的 `img/`、`thumbs/` 與 `data/`
不包含在其 MIT 程式碼授權內。本專案不重新散布這些素材。

- CharaDB：程式碼 MIT；`items_public.json` 的 tags／名稱為 CC0；圖片禁止再散布，
  本專案只讀取使用者自行安裝的檔案。
