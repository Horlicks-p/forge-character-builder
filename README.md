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
[CharaDB](https://github.com/taki-ttt/charadb)。請自行下載官方的
`data/items_public.json`，放到下列任一位置：

- `vendor/charadb/data/items_public.json`
- 或以環境變數 `CHARADB_ROOT` 指定 CharaDB 根目錄（其下的 `data/items_public.json`）

未安裝時，既有的 Pose Gallery 功能照常可用。CharaDB 的圖片包請從官方 Release
自行解壓縮，本擴充不會下載或重新散布任何圖片。

> 目前（Phase 1）只完成資料轉換與 Prompt 組裝核心，Character Builder 介面將於 Phase 2 加入。

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
