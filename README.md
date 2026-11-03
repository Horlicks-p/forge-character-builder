# AI Pose Gallery for Forge Classic

Forge Classic 原生的中文姿勢、服裝與人物參考圖庫介面，搭配
[ai-pose-gallery-zh](https://github.com/anhao/ai-pose-gallery-zh) 資料使用。

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
└─ ai-pose-gallery-forge/
   ├─ scripts/
   └─ vendor/
      ├─ data/
      ├─ img/
      └─ thumbs/
```

也可以用環境變數 `AI_POSE_GALLERY_ROOT` 指定原始資料庫路徑。

重新啟動 Forge 後，頂部會出現「AI 姿勢圖庫」分頁。

## ControlNet 建議

- 普通姿勢圖：擴充會預選 `canny`，模型請選 Canny ControlNet（例如 xinsirControlnetCanny_v20）。
- Depth 圖：擴充會預選 `None`，模型請選 Depth ControlNet。
- 服裝與人物圖：適合送到 img2img，或自行搭配 Reference / IP-Adapter。

## 授權

本擴充程式碼可獨立授權；原始專案的 `img/`、`thumbs/` 與 `data/`
不包含在其 MIT 程式碼授權內。本專案不重新散布這些素材。

