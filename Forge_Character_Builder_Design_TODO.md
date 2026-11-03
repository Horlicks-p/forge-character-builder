# Forge Character Builder 功能設計書 / TODO

> **文件版本：** v1.4（2026-11-03；見文末「修訂紀錄」）  
> **目標專案：** https://github.com/Horlicks-p/forge-character-builder  
> **目標執行環境：** [Haoming02/sd-webui-forge-classic](https://github.com/Haoming02/sd-webui-forge-classic) `neo` 分支、Gradio 4.40.0、Neo 內建 ControlNet（`sd_forge_controlnet`）。  
> **用途：** 交由 Claude Code / Codex 實作的技術規格與驗收清單  
> **文件語言：** 繁體中文（程式碼、UI 名稱及 Prompt 標籤可使用英文）  
> **專案定位：** Stable Diffusion Forge 的「角色組件 Prompt Builder + 姿勢圖 ControlNet 傳送器」，**不是**另一套生成引擎。

---

## 0. 寫給實作者的執行指示（請優先閱讀）

1. **先審查現有程式碼、分支與 PR，不要直接重寫專案。** 開工前以 GitHub 上的實際狀態為準（見下方 0.1），避免覆蓋已合併或待驗收的變更。
2. 本次任務以**新增 Character Builder、共用資料轉換層、整合現有 Pose / ControlNet** 為主；禁止不必要的大規模重構。
3. 使用者**已自行在本機放置 Anhao 圖片資料**。請勿重新下載、搬移、覆寫、刪除或提交該圖片包。
4. **所有第三方圖片必須與外掛程式碼分離。** 外掛的 Git 倉庫不得包含來自 Anhao、ギア、CharaDB 或 Anime Costume Gallery 的原圖／縮圖；不得做未經授權的批次下載或重新分發。
5. 先讓最小主線可用：**選頭髮／眼睛 → 選衣服 → 選 Pose → 合併 tags → Send All → txt2img + ControlNet 1。** 新增來源可以分階段啟用，不能因缺少某個可選資料庫而讓外掛無法啟動。
6. **唯一驗收環境為 Forge Neo**（`Haoming02/sd-webui-forge-classic` 的 `neo` 分支、Gradio 4.40.0、Neo 內建 ControlNet）及 `txt2img`。Forge Classic 與 AUTOMATIC1111 無印版**不列入 MVP 相容範圍**，不要為它們加入雙版本相容層。保留現有 `img2img` 功能，但不用新增 ComfyUI、雲端生圖 API、IP-Adapter 或 LoRA 管理介面。
   - 本文件的 Neo 原始碼審查快照為 commit `7d6fd36`（2026-11-03）。這**只是審查依據，不是永久依賴**；`neo` 仍持續更新，實機驗收時須記錄當時實際的 Neo commit SHA。
7. 變更請以**小 PR** 交付，每個 PR 有驗收步驟、測試結果與已知限制。**不要直接推送到 main**；除非使用者另行指示。

### 0.1 目前 PR 狀態（2026-11-03 查核）

| PR | 內容 | 分支 → base | 狀態 |
| --- | --- | --- | --- |
| #1 | P0：Canny 預設、Outfit Prompt 逗號格式化 | `fix/p0-canny-prompt-commas` → `main` | **已合併** |
| #2 | Phase 1：CharaDB adapter、Prompt core、更名 Forge Character Builder | `feat/phase1-charadb-prompt-core` → `main` | **已合併** |
| #3 | Phase 2：Character Builder UI + Send All（MVP） | `feat/phase2-character-builder-ui` → `main` | **開啟中（Draft）**，待 Forge Neo 實機驗收 |
| #4 | Phase 4A：Source UX（來源狀態、安裝提示、顯示設定）與 stale selection 修正 | `feat/phase4-source-ux` → `feat/phase2-character-builder-ui`（stacked） | **開啟中（Draft）**，待實機驗收；PR #3 合併後 rebase 到 `main` |

- PR #3、#4 的程式只在 Gradio 4.40 harness + Playwright 驗證過，**不等於實機驗收**；合併前提是在 Forge Neo 完成各 PR 內的驗收清單。
- Phase 3 為 **Deferred**，不阻塞其他 Phase。

## 1. 背景與需求摘要

### 1.1 目前的實際使用方式

- 使用者主要用 Stable Diffusion WebUI Forge／Forge Neo、SDXL Illustrious 系 Checkpoint、LoRA、`txt2img`、ControlNet。
- 經驗證的 Pose 使用方式：人體結構參考圖 → ControlNet `canny` → 相容的 Canny 模型（使用者曾用 `xinsirControlnetCanny_v20`）。
- 既有外掛已能瀏覽本機 Anhao 素材（Pose / Outfit / Portrait）、搜尋、選圖、追加 prompt、送到 `txt2img + ControlNet 1`。
- 目前的缺點：人像參考不是主要需求；原本 Outfit 關鍵字偏向「讓圖片模型看參考圖」的簡略描述，不一定適合 Illustrious 的 Danbooru-style Prompt；髮型／眼睛／服裝缺少跨分類的組裝介面。
- 使用者通常生成**已存在的動漫／遊戲角色**，可能已有角色專屬 LoRA 和 Prompt。外掛不應強迫加入固定身份、髮色、角色名或通用品質標籤。

### 1.2 產品目標

**從同一個 Forge 外掛挑選：**

1. **CharaDB**：髮型、前髮、髮色、髮飾、眼睛顏色、眼睛形狀。
2. **CharaDB + Anime Costume Gallery**：服裝；可選擇保留既有 Anhao 寫實服裝資料作為第三來源。
3. **原有 Anhao Pose 資料庫**：選 Pose 與原圖／Depth（未來可加 CharaDB Pose 線稿為選用來源）。
4. **按「Send All」**：把「角色外觀 tags + 衣服 tags」（MVP 不含動作 tags）**追加**至現有 `txt2img` 正向 Prompt，並將選定的 Pose 圖送至 **ControlNet Unit 1**；不改寫既有 LoRA、Negative Prompt、Checkpoint、CFG、Sampler、解析度等設定。

### 1.3 不做的事（Non-goals）

- 不做 AI 雲端圖片生成器，也不建立 ComfyUI 工作流程編輯器。
- 不要求選擇人像、真人臉或身份參考圖；`Portraits` 改為選用／預設隱藏，但**不得刪除既有資料**。
- 不做自動推斷完整服裝描述的 LLM 服務；不要每按一次選圖就調 API。
- 不自動下載整個新增圖片站點，不打包第三方圖片進 Git，不假設圖片能合法隨外掛發行。
- 不自動切換 ControlNet 模型名稱、權重或其它使用者設定；模型相容性只提供提示／檢查。

---

## 2. 資料來源：分工、數量、格式與授權

> 數量為 2026-11-03 查核結果，未來可變動，**UI 不得將數量寫死**。所有來源必須分配獨立 `source_id` 與原始 `item_id`，避免同名或重複素材混淆。

| 來源代號 | 官方／參考位置 | 約略項目數 | 使用角色 | 核心資料 | 圖片策略 |
|---|---|---:|---|---|---|
| `anhao_pose` | https://github.com/anhao/ai-pose-gallery-zh | 1,439 個姿勢視角紀錄 | 主要 Pose + Depth | `data/pose-zh.json` | 沿用使用者已備妥的本機 `vendor/img`、`vendor/thumbs` |
| `charadb` | https://github.com/taki-ttt/charadb | 1,027 筆：髮／眼 306、服裝 440、姿勢 281 | 角色外觀與服裝 tags；Pose 為可選擴充 | `data/items_public.json`（`set`、`sub`、`jp`、`tags` 等） | 使用者自行從官方 Release 安裝圖片；或有條件單張按需參考，**不由外掛批量下載** |
| `anime_costume` | https://ai-costume-anime-gallery.pages.dev/ | 約 1,320 套 | 另一個 Anime Outfit 圖片庫 | 需先核對官方可用的 metadata 結構／可信快照 | 優先遠端單張縮圖／預覽，**不得批量鏡像** |
| `anhao_outfit` | 同 Anhao | 1,407 套 | 相容性與選用寫實服裝庫 | `data/costume-zh.json` | 已有本機素材，保留但預設可隱藏 |
| `anhao_portrait` | 同 Anhao | 1,499 張 | Legacy 功能 | `data/portrait-zh.json` | 預設隱藏，仍可選擇啟用 |

### 2.1 重複來源判定（重要）

- **Anhao 的 `data/costume-anime-zh.json` 已收錄 CharaDB 440 套服裝的漢化索引**，不是另一批 440 套；若使用該檔作本地中文對照，與 CharaDB 原始 `Oxxx` 項目應去重。
- `AI Costume Anime Gallery` 是ギア系列的**約 1,320 套另一組動漫版服裝**，與 CharaDB 的 440 套不同；其 1,320 套主要對應原有寫實服裝的動漫化表現，**不要再與寫實素材直接相加當成全新衣服設計數**。
- `hongye007/open-pose-costume` 有整合這 1,320 套的公開說明，可供參考其 metadata 建模方式，但**不要因此整合整個網站或其自建素材**。其新增自建圖片有另外的授權條件。

### 2.2 CharaDB 原始資料欄位與注意事項

官方索引 `data/items_public.json`：

```json
{"id":"H001","set":"hair","cat":"髪型","sub":"style","jp":"ロングヘア","tags":"long hair","color":"brown hair"}
{"id":"H001-black","set":"hair","cat":"髪型","sub":"style_color","jp":"ロングヘア(黒)","tags":"long hair","parent":"H001","color":"black hair"}
{"id":"O001","set":"outfit","cat":"制服・学生","jp":"セーラー服(紺)","tags":"serafuku, sailor collar, blue sailor collar, red neckerchief, pleated skirt, blue skirt"}
{"id":"P002","set":"pose","cat":"立ち","jp":"腰に手","tags":"standing, hands on hips"}
```

查核到的 `hair` 子類別：`style` 46、`style_color` 184、`bangs` 12、`color` 20、`acc` 16、`eye` 28（眼睛顏色 19 + 瞳型 9），合計 306。**不要把 `style` 與 `style_color` 重複顯示成不同髮型**；要把它們整理成「髮型 + 色彩變體」。

- `tags` 已是 Danbooru-style，**原樣保留標籤意義，不要套用 Anhao 寫實服裝的空白分隔演算法**。
- 髮型款（`sub=style`）的 `color` 欄位只是官方示意圖的髮色，**預設不輸出**。只有使用者明確選擇色彩變體（`style_color`）或 `Hair Color` 時才輸出髮色；兩者同時存在時以 `Hair Color` 為準，不能輸出互相矛盾的 `brown hair, black hair`。理由：使用者多半生成已有角色 LoRA 的既有角色，自動帶出預設髮色會與 LoRA 衝突（見 §1.1）。
- 眼睛顏色和眼睛形狀分開選擇；必要時由 `cat` 判斷 `目の色` / `瞳の形`。
- `parent` 是變體關係。圖片路徑規則**應以官方 Release 實際解包結構做驗證**，不可把推測的路徑當作已存在的檔案。
- 上方範例以單行物件示意，**實際檔案可能是 JSON array 而非 JSON Lines**；adapter 以實際檔案格式為準，兩者都能容錯更佳。
- CharaDB 沒有成人分類（`部屋着・パジャマ` 是居家服）。Safe Mode 以少量明確 tags 判定（`micro bikini`、`slingshot swimsuit`、`see-through`，目前命中 O224／O229／O231），與 Anhao「性感穿搭」過濾的尺度一致（見 §4.6）。
- **實際資料查核（Phase 1）：**
  - 瞳型項目帶有示意用眼色（如 `tareme, brown eyes`）。與髮型預設色同理，**正規化時移除眼色 tags**，眼色只來自 `Eye Color` 欄位。
  - 少數服裝帶有髮色（O130 `black hair`、O314 `white hair`）。**正規化時移除髮色／眼色 tags**，避免覆蓋角色或 LoRA；`raw_tags` 仍保留原文供預覽。
  - `style_color` 變體只有 4 色（black／blonde／pink／silver），每個髮型都有，故 UI 以 `Hair Color` Dropdown 為主，變體主要用於縮圖。

### 2.3 AI Costume Anime Gallery

- 將此來源視為獨立 `anime_costume` Provider，支援**分類、關鍵字、縮圖、原圖 URL、來源連結與可用 Prompt 片段**。
- 公開資訊顯示約 1,320 套、春夏秋冬等 8 類，與原本寫實服裝有對應編號。**正式匯入前要實際驗證**項目 ID、URL 命名、英文關鍵字、圖片可用性及來源條款；目前不可宣稱已取得完整官方 JSON。
- 這套的英文關鍵字可能是**沒有逗號的字串**，也可能包含非 Danbooru tags；第一版應套用保守的衣物詞組格式化，並保留 `Raw Keywords` 供使用者檢查、手動修正。**不要臆測圖片看不到的衣物細節**。
- 取得來源資料的優先順序：**官方允許使用的 metadata** → 經確認來源與授權的本地 metadata 快照 → 必要時經維護者同意的人工整理；**不得為了補 metadata 而直接批次抓取圖片**。
- 網站不可用／跨域／縮圖失敗時，UI 顯示「無法預覽／來源離線」，不阻止其他來源使用。不應將遠端 URL 交給 `PIL.Image.open()` 當本機檔案。

### 2.4 授權規則（必須實際落實）

- **CharaDB：** 程式碼 MIT；`data/items_public.json` 中 tags／日文名稱按 LICENSE 為 CC0；圖片另有限制，禁止原圖與加工圖再分發／轉載／轉售，且有原作者明列的用途限制。官方 Release 雖提供圖片包，不代表允許將其二次上傳到自己的 GitHub。
- **ギア系列（原有 Pose／寫實服裝／Anime Costume）：** 原站允許單張參考作畫，但圖片資料本身禁止重新散布／販售；其站點條款還限制自動批量下載。**外掛不得內建 bulk image downloader、鏡像器或對外公開圖片快取。**
- **Anhao 整理版：** 程式碼授權不自動涵蓋其引用的圖片及原始資料；若使用漢化索引，注意其資料／翻譯的標示與授權。
- **分發模式：** Git 僅提交 adapter 程式碼、必要的可合法再分發 metadata、文件與測試用*自製*微型 fixtures；第三方圖片由使用者自行在本機配置，或依來源允許的方式單張按需顯示。
- 發布 README 的 `Sources & Licenses` 章節、每個 Provider 的來源網址、素材與程式碼不同的授權條件；不要以「開源」誤導為全部素材可自由轉載。

---

## 3. 使用者流程（MVP）

```text
啟動 Forge → 打開「Character Builder」分頁
        │
        ├─ Step 1：Character（CharaDB）
        │    ├─ Hair style + Hair color
        │    ├─ Bangs + Hair accessory（可不選）
        │    └─ Eye color + Eye shape（可不選）
        │
        ├─ Step 2：Outfit
        │    ├─ CharaDB 440（優先，直接用 tags）
        │    ├─ Anhao 既有服裝（可選／Legacy）
        │    └─ AI Costume Anime Gallery 1,320（Phase 3 非阻塞擴充，可能取消）
        │
        ├─ Step 3：Pose（Anhao 現有資料庫）
        │    ├─ 搜尋／篩選／縮圖／選定
        │    └─ Normal/Canny | Depth（保持原有功能）
        │
        └─ Final Prompt Preview（可編輯）
             ├─ Copy
             ├─ Append to txt2img
             ├─ Send Pose to ControlNet 1
             └─ Send All（一次送 Prompt + Pose）
```

**成功標準：** 使用者無須切換其他網站，不用複製三次文字，即可在 Forge 原有 `txt2img` 正向 Prompt 看見簡易 tags，同時在 ControlNet Unit 1 看見所選 Pose 圖。原有 LoRA 與所有非目標設定保持不變。

---

## 4. UI / UX 詳細規格

### 4.1 主介面架構

**沿用現有的頂部分頁（已更名為 `Character Builder`）**，在其中以 `gr.Tabs` 或等價的穩定 Gradio 元件分成：

- **Character Builder（預設）**：三步選擇器 + 最終 Prompt 預覽 + Send All。
- **Pose Gallery（Legacy）**：繼續保留既有直接選圖、送到 ControlNet、送 `img2img` 流程。整合完成且確認無回歸後，才考慮合併為單一 UI。
- **Settings（簡潔）**：來源可用狀態、路徑設定說明、Portraits / Anhao Outfits 是否顯示；**第一版不需新的設定資料庫**。

Neo 使用 Gradio 4.40.0，巢狀 `gr.Tabs` 可正常使用；**仍以實際相容性優先，不為了 UI 美觀破壞現有功能**。

### 4.2 Step 1 — Character

- **只有 `Hairstyle` 使用圖片 Gallery**（`charadb` 縮圖、英文 tags、項目 ID；可依 `cat` 的英文分類篩選）。未安裝 CharaDB 圖片包時，退化為文字清單／Dropdown 仍可選擇。
- 小型分類改用 **`gr.Dropdown`**（選項含 `None`，顯示英文 tags）：`Hair Color`（約 20）、`Bangs`（約 12）、`Hair Accessory`（約 16）、`Eye Color`（約 19）、`Eye Shape`（約 9）。數量以實際 catalog 為準，不寫死。
- `Hairstyle` 選定後，若有 `style_color` 變體，可提供變體 Dropdown；選變體等同指定髮色。
- 每個欄位旁顯示選中名稱與 tags，提供 `Clear`；不可選擇的欄位保留空值。
- 使用已存在的動漫角色時，能夠不選髮型／眼睛，只選服裝與 Pose。
- `style_color` 色彩變體需與 `style`／`color` 正確配對；清除時不能留下舊髮色。只選髮型、未選任何髮色時，**不輸出髮色 tag**（見 §2.2）。
- `Hair Accessory` 當前以單選實作，多選可後續擴充。

### 4.3 Step 2 — Outfit

- 服裝使用圖片 Gallery。來源切換：`CharaDB (Anime)`／`Anhao (Original)`；Phase 3 若完成再加入 `Anime Costume Gallery`。`Anhao (Original)` 可在設定中隱藏，但不刪除。
- 來源、分類、搜尋、縮圖、名稱、tags、來源連結；每次只選一套 Outfit（MVP），避免多套衣服同時互相衝突。
- 只在使用者確實選用時追加服裝 Prompt；可手動編輯輸出。
- **CharaDB：** tags 已有逗號，不可再逐字拆詞。
- **Anime Costume Gallery / Anhao：** 優先使用結構化 tags 或保守格式化；保留 Raw 與 Formatted 預覽，對不確定的詞不強行擴寫。
- 缺圖與離線時須清楚標示，並且不會讓整個 Builder 失效。

### 4.4 Step 3 — Pose

- 沿用 Anhao `pose-zh.json`、既有 Gallery 分頁與 safe mode 篩選；保留完整編號、原始名稱、視角、分類。**同一姿勢只佔 Gallery 一格，依 `(體系, 姿势编号)` 分組，避免不同體系的 `P001` 相撞；選取後顯示實際可用的四／五個角度，沒有的角度不可造假。**
- 選定 Pose 後在 Builder 中顯示縮圖、詳細資訊、`Normal` / `Depth` 切換。
- **MVP 的 Pose tags 預設留空**。現有 `_prompt_for()` 對 Pose 的輸出（如 `Front view, Standing, Single-person full body — standard, character pose reference`）不是 Danbooru tag，部分翻譯還帶 NSFW 語意，**不得帶入 Builder 的 Final Prompt**。Pose 的作用由 ControlNet 承擔；使用者需要時可在 Final Prompt 手動補動作 tags。
- 小型可靠對照表（如 `站姿 → standing`、`侧面 → from side`、`俯视 → from above`、`仰视 → from below`）列為 Phase 4，須先確認 Anhao 欄位語意後才啟用。
- `Normal` → 控制圖片與 `canny` Preprocessor；`Depth` → 對應深度圖與 `None` Preprocessor；使用者自行選相容的 ControlNet 模型。
- 不自動替換 ControlNet 的模型或其它參數；如果選到 Depth 卻搭配 Canny 模型，顯示相容性提醒。
- 可選的未來擴充：`CharaDB Pose` 281 項，若官方線稿已由使用者手動安裝，提供 `lineart` / `canny`；**MVP 可先不做**。
- 第一階段先確保可送圖，不能把臆測動作加進 tags。

### 4.5 Final Prompt 預覽與控制

- 三段預覽：`Appearance`、`Outfit`、`Final Prompt`（MVP 的 Pose 不產生 tags；Phase 4 若啟用對照表再加 `Pose` 段）。
- Final Prompt 在送出前**可編輯**；如果使用者手動修改，除非按 `Reset from selection`，不可因為切換 UI 而無提示地覆蓋手動內容（可用簡單 `dirty` state 防護）。
- 按鈕：`Copy Prompt`、`Append Prompt to txt2img`、`Send Pose to ControlNet 1`、**`Send All`**。`Replace txt2img Prompt` 可以是非預設的進階按鈕，務必明確標示會覆寫。
- `Send All` 是主要 CTA；Pose 未選時應禁用或提示使用者改按 `Append Prompt`，不要默默在沒有 Pose 時宣稱已完成 ControlNet 傳送。
- 僅使用英文 Danbooru-style Prompt。
- **UI 介面全英文**（標籤、按鈕、分類、狀態訊息、Dropdown 選項、卡片標題）。來源的日文／中文名稱**不顯示在 UI**，只保留在 `metadata` 供除錯與搜尋：
  - CharaDB 髮／眼項目：顯示名稱用其英文 tags（如 `long hair`、`tareme`），必要時附 ID。
  - CharaDB 服裝：顯示 `ID · 前幾個 tags`（如 `O001 · serafuku, sailor collar…`）。
  - CharaDB／Anhao 分類：以英文對照表顯示（沿用現有 `VALUE_TRANSLATIONS` 的作法），對照表缺項時顯示 ID 或 `Other`，不顯示原文。
- 每筆圖片可有 `Open Source` 連結；不強制 `Send All` 同時送三張參考圖——**只有 Pose 進 ControlNet，髮型／眼睛／服裝走 Prompt**。

### 4.6 安全模式與資料呈現

- 保留原本 SFW 預設開啟及分類過濾；新的服裝 Provider 也要映射成人類別，而不是只過濾 Anhao。
- 安全模式開啟時，已選但被切掉的素材要清除選擇，提示使用者；不得保留隱藏素材繼續送出。
- 若 Final Prompt 曾手動編輯（dirty），清除 Outfit 後**保留**使用者文字，但必須提示「可能仍含已清除服裝的 tags」，不得只說已清除。Pose 在 MVP 不輸出 tags，清除 Pose 不需此提示。
- 切換 Outfit 來源（CharaDB／Anhao）時，清除另一來源的 Outfit 選擇；Final Prompt 依 dirty-state 規則處理（未編輯則重建，已編輯則保留並提示）。
- Legacy Pose Gallery 的選取只記錄「library + ID」，送出時才依**當下的** Normal／Depth 一起決定圖片與 Preprocessor，兩者不可能不一致；切換 Normal／Depth 時預覽同步更新；Safe Mode 開啟後不得送出已隱藏的項目。
- 縮圖分頁／按需載入；至少保持目前「每頁 48 張」的操作上限或等效虛擬列表；不要一次向 Gradio 傳入千張圖片。
- 計數從啟用的來源與實際 catalog 算出，不要保留寫死的 `4,345 REFERENCE IMAGES`。

---

## 5. Prompt Builder 設計規範

### 5.1 最終 Prompt 預設順序

```text
[optional subject tags], [hair color], [hair style], [bangs],
[hair accessory], [eye color], [eye shape],
[outfit tags]
```

（`[pose tags]` 在 MVP 恆為空；Phase 4 啟用可靠對照表後才附加於最後。）

- 預設**不**加 `masterpiece`、`best quality`、Checkpoint 專用 `score_*`、LoRA、角色名，避免與使用者原有 Prompt 衝突。
- `1girl` / `solo` 可為明示選項（預設 OFF）；**不要**把原本角色強行改成一般原創角色。
- 各欄位為空就跳過；不能留下雙逗號、尾端逗號或多餘空白。
- 以英文半形逗號加一個空格分隔 `, `；保留多字詞組，例如 `long hair`、`pleated skirt`、`white sneakers`。
- 如果不同欄位產生完全相同 tag，做**完整 token 層級去重（忽略大小寫及首尾空白）**，不要用 substring 去重（`skirt` 與 `pleated skirt` 是不同 tag）。
- 正確解決互斥顏色：變更髮色時，移除舊的髮色 tag；不能把衣物顏色錯誤地刪掉。
- 保留使用者已輸入的 `(<tag>:1.2)`、`<lora:...:1>`、`BREAK` 等 Forge Prompt 語法；**只處理我們自己產生的片段，不能清洗整個 txt2img 內容**。
- （選做）若追加片段的 tag 與 txt2img 既有內容重複，或髮色／眼色與既有內容衝突，可顯示**提示**；不得自動刪改既有內容。

### 5.2 原始、格式化與手動修改

每項資料建議有：

- `raw_tags`: 來源原始內容；
- `normalized_tags`: 格式化後清單（供組裝）；
- `edited_prompt`: 本次會話使用者在 Final Preview 的手動覆寫（若有）。

**CharaDB：** `tags.split(',')`（配合 trim + 清空白）即可，勿重跑針對 Anhao 的英文空白詞切分。`Hair style + color` 需要專門合併器。

**Anhao 及 Anime Costume：** 先讀取原始結構，若英文 keywords 無逗號，採**保守**的衣物名詞邊界規則，無法確認則保留原字串，讓使用者編輯；避免把 `mini skirt`、`blue sailor collar`、`white sneakers` 錯分。

### 5.3 範例（僅示意，不代表固定強制內容）

選擇 Hairstyle `H001`（long hair）、Hair Color `black hair`、Bangs `blunt bangs`、Eye Color `brown eyes`、CharaDB Outfit `O001`、任一 Anhao Pose：

```text
black hair, long hair, blunt bangs, brown eyes,
serafuku, sailor collar, blue sailor collar, red neckerchief,
pleated skirt, blue skirt
```

Pose 只送 ControlNet，不產生 tags。若只選 `H001` 而未選髮色，輸出為 `long hair, ...`，**不含**示意圖預設的 `brown hair`。

這段是**可追加片段**，不是完整生圖配方；它不包含角色 LoRA、品質標籤、背景或 Negative Prompt。

### 5.4 Append / Replace 的行為

- **預設 Append**：保留 txt2img 已有內容；非空時先判斷結尾是否已含逗號，再只加入必要的 `, `。
- 不能更改 Negative Prompt，不能跳轉到錯誤的 img2img textarea。
- 如果重複按同一份 `Send All`，應避免連續追加相同片段：可記錄本次會話的最後一次附加片段，若它仍完整位於 prompt 尾端就提示「已追加」或略過；如果使用者手動編輯 Prompt，不能武斷刪除原文。
- **Replace**（若有）：明確告知會覆蓋正向 Prompt，預設不要執行。
- 每種按鈕的狀態與成功／失敗訊息要能明確區分：`Prompt appended`、`Pose sent`、`ControlNet unavailable`、`Missing pose image`。

---

## 6. ControlNet 整合規格

現有程式透過 `lib_controlnet.controlnet_ui.controlnet_ui_group.ControlNetUiGroup` 找到 txt2img 對應群組，對 `target.image.background`、`target.enabled`、`target.module` 輸出。

**已依 Neo 原始碼（審查快照 `7d6fd36`）確認：**

- Neo 的 ControlNet 圖片元件是 `ForgeCanvas`，其 `background` 為 `LogicalImage(numpy=True)`；現有傳 numpy array 至 `target.image.background` 的寫法正確。
- Neo 的 Unit 分頁標籤為 `ControlNet Unit {i + 1}`，即 UI 從 **Unit 1** 開始；`ControlNetUiGroup.all_ui_groups` 中第一個 `is_img2img == False` 的 group 就是 txt2img 的 Unit 1。
- Gradio 4.40.0 支援 `.success()`。

### 6.1 預設與例外

| 圖片類型 | Preprocessor | Model | 備註 |
|---|---|---|---|
| Anhao Pose Normal | `canny` | 使用者手動選相容 Canny 模型 | P0 PR 已處理預設；確認分支狀態 |
| Anhao Pose Depth | `None` | 使用者手動選 Depth 模型 | 素材已是 Depth 圖，避免再處理 |
| CharaDB Pose lineart（未來） | 可選 `lineart` 或 `canny`，需實測 | 手動選對應模型 | 不作 MVP 的阻塞條件 |
| CharaDB Hair / Eye / Outfit | 不送 ControlNet | 不適用 | 只送 Prompt |
| Anime Costume Gallery | 不送 ControlNet | 不適用 | 只送 Prompt |

### 6.2 Send All 的交易順序

1. Preflight：確認選中 Pose，原圖／Depth 圖確實存在且可讀，ControlNet txt2img 對應 Unit 1 可取得。
2. 確認最終 Prompt 非空、元件仍有效、Safe Mode 不違反當前選項。
3. 傳 Pose 圖至 ControlNet 1；預處理器依 Pose 模式設定；`enabled=True`。
4. **確認 ControlNet 傳送步驟成功後**，將最終 Prompt 追加到 txt2img；切至 txt2img。
5. 回報兩步驟是否成功；遇到錯誤應停止並顯示原因，避免只送了一部分卻顯示「全部完成」。

**實作規定：** 步驟 3 → 4 必須用 Gradio 的 **`.success()`** 串接，**不得使用 `.then()`**。`.then()` 不論前一步成功或失敗都會執行，會在 ControlNet 失敗時仍追加 Prompt。Preflight 失敗時以 `gr.Error` 中止，使 `.success()` 後續不執行。現有 Legacy Gallery 的 `Send to txt2img + ControlNet 1` 按鈕也是 `.then()`，須一併改為 `.success()`。

若 Gradio / Forge 綁定無法精準做到原子性，採「先驗證、後依序處理、逐步回報」；不做危險的自動回滾或擅自重設使用者原有 ControlNet 設定。

### 6.3 模型相容與多 Unit 問題

- **Unit 1 的定義：** `ControlNetUiGroup.all_ui_groups` 中第一個 `is_img2img == False` 的 group，即 Neo UI 上的 `ControlNet Unit 1`（txt2img）。現有選取邏輯即符合此定義，沿用即可。
- 切勿為了 Canny 而擅自替換目前選中的模型。遇到 Canny + Depth 模型組合，應顯示警告，不宣稱模型匹配。
- 若 ControlNet 擴充不存在／API 不相容，Character Builder 仍可使用 `Copy`／`Append`，`Send Pose`／`Send All` 要禁用或顯示可理解錯誤，不得使整個 Forge 啟動失敗。

---

## 7. 建議程式架構（不強制一模一樣）

> 核心原則：**資料 adapter / Prompt Builder / Forge 傳送層 分離**。目前 `scripts/character_builder.py` 約為單檔 Gradio 實作，可用薄層拆分；勿導入 React、Vite 或外部狀態管理框架。

**共用模組的位置（重要）：** Forge 只載入 `scripts/` 第一層的 `.py`，並以 `spec_from_file_location` 逐檔載入，所以**不要**把套件放在 `scripts/` 底下。共用模組放在**擴充根目錄**，使用唯一的套件名 `character_builder_core/`，避免與其他擴充撞名。Neo 的 `modules/scripts.py` 會把每個擴充的根目錄加入 `sys.path`，入口檔可直接 `from character_builder_core import ...`，**不需手動修改 `sys.path`**。`character_builder_core/` 內不得 import `gradio` 或 `modules`，以便在無 Forge 環境下用純 Python 測試。

```text
forge-character-builder/
├── scripts/
│   └── character_builder.py        # 既有 Forge tab 入口 / UI，避免巨幅重寫
├── character_builder_core/        # 新增純 Python 共用邏輯（擴充根目錄、唯一名稱）
│   ├── __init__.py
│   ├── models.py                   # CatalogItem、BuilderSelection 等資料結構
│   ├── catalog.py                  # Provider 匯入 / 索引 / 篩選
│   ├── charadb_adapter.py          # items_public.json 轉換
│   ├── anhao_adapter.py            # pose/costume/legacy portrait
│   ├── anime_costume_adapter.py    # Phase 3（非阻塞，可能取消）
│   ├── prompts.py                  # Danbooru tags / merge / 去重；含 P0 的 outfit 格式化
│   └── assets.py                   # 本機路徑、來源 URL、安全檢查
├── javascript/
│   └── character_builder.js        # txt2img append 與 tab 切換（沿用／少量補強）
├── style.css                      # 既有 UI 樣式
├── tests/
│   ├── test_charadb_adapter.py
│   ├── test_prompt_builder.py
│   ├── test_catalog.py
│   └── test_asset_resolver.py
├── vendor/                        # 使用者本機素材；Git ignore，不散布
│   ├── data/                       # 既有 Anhao JSON
│   ├── img/                        # 既有 Anhao 圖
│   ├── thumbs/                     # 既有 Anhao 縮圖
│   └── charadb/                    # 使用者從官方 Release 自行解包（可選）
│       ├── data/items_public.json  # 可改採獨立資料路徑，依實際封包為準
│       ├── img/
│       ├── thumb/
│       └── line/
├── README.md
└── docs/
    ├── SOURCES_AND_LICENSES.md
    └── CHARACTER_BUILDER.md
```

可支援（非必須）路徑設定：

```text
ANHAO_GALLERY_ROOT=<既有 Anhao 根目錄>     # 舊名 AI_POSE_GALLERY_ROOT 仍可用（向下相容）
CHARADB_ROOT=<使用者自行解包的 CharaDB 根目錄>  # 新增，可選
ANIME_COSTUME_METADATA=<經確認的本地 metadata JSON>  # 新增，可選
```

### 7.1 正規化資料模型（概念）

```python
@dataclass(frozen=True)
class CatalogItem:
    source_id: str                  # anhao_pose / charadb / anime_costume / ...
    item_id: str                    # 原始 ID；主鍵實際使用 (source_id, item_id)
    kind: str                       # hair / eyes / outfit / pose / portrait
    category: str
    display_name: str
    raw_tags: str
    normalized_tags: tuple[str, ...]
    thumbnail: str | None           # 本機路徑或可允許的 HTTPS URL
    image: str | None
    depth_image: str | None
    metadata: dict                  # 原始欄位、source URL、變體等
```

`BuilderSelection` 不應只記圖片路徑，須記**來源 + item ID + 使用中的 variant / depth mode**，才能安全地更新 Prompt、清空分類、儲存當次選擇。

### 7.2 資料 / 路徑安全

- 所有本機圖片路徑需在允許的資料根目錄之下，不接受 `../`、絕對路徑跳脫或外部 URL 偽裝本機路徑。
- 任何遠端縮圖只使用核准 HTTPS 網域，避免任意 URL、重定向到內網、過大的回應或無限重試；如須後端單張讀取，限制 timeout、size、MIME、cache 策略，並再次確認來源條款。
- 原始素材目錄可唯讀處理；不得在下載／匯入時覆寫第三方檔案。找不到資料庫時正常降級（disabled Provider + 指引），而不是 `ImportError` 或 UI 空白。

---

## 8. 分階段 TODO / PR 計畫

### Phase 0 — Baseline & P0 整合（先做）

- [x] 確認 `main` 與 PR #1 的實際狀態，整合 `canny` 預設與 Outfit 逗號分隔，不得重覆實作衝突版本。（PR #1 已合併）
- [ ] 在 Forge Neo 實機記錄當時的 Neo commit SHA，以及 ControlNet preprocessor Dropdown 中 `canny` 的實際名稱；保留可回滾的原始狀態。
- [x] 將現有 `Send to txt2img + ControlNet 1` 的 `.then()` 改為 `.success()`（已隨 PR #2 合併；待實機驗證）。
- [x] 確認 `vendor` 資料目錄未被 Git track；檢查 `.gitignore`。（`.gitignore` 含 `vendor/`）
- [ ] 先執行現有功能的 smoke test：Pose browse / Depth / Append Prompt / Send to txt2img ControlNet / img2img。

**完成條件：** 原有外掛在新增模組前就可啟動，Pose Normal / Depth 路徑與既有傳送行為正常。

> 實機 smoke test 需要 Forge Neo 環境；無環境時可先進行 Phase 1（純 Python），Phase 0 的實機項目待有環境時補做。

### Phase 1 — CharaDB Metadata Adapter + Prompt Core（第一個功能 PR）

- [x] 讀取使用者自行放置的 `items_public.json`；無檔案時顯示提示，不影響 Anhao。
- [x] 建立根目錄套件 `character_builder_core/`；將 P0 的 `_format_outfit_keywords()` 移入 `prompts.py`，入口檔改為 import。
- [x] 依 `set`、`sub`、`cat` 正規化髮型、顏色、前髮、髮飾、眼色／瞳型、服裝；JSON array／JSON Lines 皆能讀取。
- [x] `style_color` 與 `style`／獨立顏色欄位合併，不重複羅列髮型；髮型的預設 `color` 不輸出。
- [x] CharaDB 服裝分類對應 Safe Mode NSFW 規則。
- [x] 實作純函式 `build_character_prompt(selection)`、`split_tags()`、`dedupe_tags()`、`merge_tags()`。
- [x] CharaDB 原始逗號 tags 保留多字詞組、顏色選擇不互相衝突。
- [x] 增加不依賴 Gradio / Forge 的單元測試與少量自製 fixture。

> 狀態：**已合併**（PR #2，2026-11-03）。40 項單元測試通過，含對真實 `items_public.json`（1,027 筆）的驗證。

**完成條件：** 可在純 Python 測試中從 CharaDB 真實格式選出 hair + eyes + outfit 的合理 tags，空選擇與顏色變更都正確。

### Phase 2 — Character Builder UI + 現有 Pose（第二個功能 PR；MVP）

- [x] 增加 Character Builder 介面（Step 1/2/3 + Final Preview）。
- [x] Hairstyle / Outfit 用圖片 Gallery；Hair Color、Bangs、Hair Accessory、Eye Color、Eye Shape 用 Dropdown。支援搜尋、選擇、清空及預覽（本機圖片缺少時可顯示 no-preview）。
- [x] 沿用 Anhao 的 Pose browser／Normal/Depth、ID、safe mode；可以在 Builder 選 Pose；Pose 不產生 tags。
- [x] 支援 `Copy`、`Append Prompt`、`Send Pose`、**`Send All`**；Send All 以 `.success()` 串接。
- [x] `Send All` 的預處理器正確，保留既有 Prompt、LoRA、Negative Prompt、ControlNet 模型。
- [x] 既有 Gallery 操作保持可用；不要直接刪除 Portraits 或 img2img 功能。

> 狀態：**PR #3 開啟中（Draft）**。程式已在分支 `feat/phase2-character-builder-ui` 完成，並以 Gradio 4.40 模擬 Forge 的 harness + Playwright 驗證；上列勾選代表 harness 驗證通過。**實機 Forge Neo 驗收（下方完成條件）仍待完成，為合併前提。**

**完成條件：** 在實際 Forge Neo + ControlNet 上完成一輪「Hair + Eyes + CharaDB Outfit + Anhao Pose → Send All（`.success()`）→ txt2img Prompt + ControlNet Unit 1」，並記錄驗收時的 Neo commit SHA。這是本專案**最重要的 MVP 交付點**。

### Phase 2B — Anhao 多視角姿勢（獨立 PR；待實機驗收）

> 分支：`feat/pose-view-grouping`，由 Phase 4A 的 `feat/phase4-source-ux` 分出；**不要在 PR #3/#4 尚未驗收時直接合併 main**。

- [x] `pose-zh.json` 依 `(體系, 姿势编号)` 分組；缺少編號時按原 item ID 個別顯示。
- [x] Character Builder 的 Pose Gallery 一組只佔一格，新增 View angle 控制；切換更新實際 `item_id` 與預覽。
- [x] Legacy Pose Gallery 亦加入 Pose view 選擇器；切換後圖片、Prompt 和送圖的來源一致。
- [x] 四視角資料僅列出實際存在的角度；Normal／Depth 均在送出時解析當下選定圖片。
- [x] 新增純 Python 測試案例（自製資料：分組、缺角度、跨體系防護、ControlNet 圖片路徑）。
- [ ] 執行完整 unittest 與 Gradio 4.40 harness / Browser UI 驗證；確認角度切換與回呼不發生競態。
- [ ] 在 Forge Neo 實機驗收 Builder 與 Legacy 兩種角度選擇，測試 Normal／Depth、Send All、Safe Mode。
- [ ] 依 PR #3 → PR #4 → 本 PR 的順序驗收與合併，必要時 rebase。

### Phase 3 — AI Costume Anime Gallery 來源（非阻塞擴充，可能取消）

> **狀態：Deferred（2026-11-03）。** 本 Phase 非阻塞，暫不實作，直接進行 Phase 4A；日後若重啟，需先完成下列授權與 metadata 查證。

> 此 Phase **不是**最終完成判定的必要條件。若查證後官方 metadata 不可得、授權不允許，或只能靠批量抓取取得資料，**取消本 Phase** 並在 README 記錄原因。

- [ ] 查證官方網站的實際 metadata、圖片可用性、ID／分類與現行授權條款；記錄來源與查核日期。
- [ ] 建置 `anime_costume` 獨立 Provider，可載入合法的 metadata；與 CharaDB、Anhao 服裝分離。
- [ ] 支援英文關鍵字保守格式化，顯示 `Raw / Formatted`，使用者可修改。
- [ ] 來源離線或圖片無法載入時，保留其他來源可用；不得因為遠端 403／CORS 造成整個 UI 崩潰。
- [ ] 只允許在符合來源條款下按需瀏覽／使用單張資料；不新增一鍵下載所有圖片。
- [ ] 加入分類、搜尋、來源 URL 與必要的授權告知。

**完成條件：** 能切換使用 CharaDB 440 與 Anime Costume 約 1,320 的服裝，選出的 Prompt 可替換 Outfit 部分，Pose 與 ControlNet 完全不受影響。

### Phase 4 — UX / 相容性改善（選做，MVP 後）

> Phase 4A「Source UX」：**PR #4 開啟中（Draft）**，分支 `feat/phase4-source-ux`（stacked on PR #3），完成前兩項，並修正 4.6 所列的三個 stale selection 問題。其餘項目**暫緩，待 PR #3 實機驗收後**再評估。

- [x] Portraits 預設隱藏但可啟用；Anhao 寫實 Outfit 可啟閉。（Phase 4A：Forge Settings，需 Reload UI）
- [x] 動態來源數量、來源健康狀態、清楚的資料安裝提示。（Phase 4A）
- [ ] 收藏／最近選用的素材（先考慮簡單本地設定；不用建資料庫）。— 暫緩：需先定 localStorage 格式與改名遷移。
- [ ] 增加可選的 CharaDB Pose + lineart 模式與模型相容提醒。— 暫緩：涉及 ControlNet，待 PR #3 實機驗收。
- [ ] Pose tags 小型可靠對照表（如 `站姿 → standing`、`侧面 → from side`）：先確認 Anhao 欄位語意再啟用，預設仍可關閉；不臆測。— 暫緩：需重新查證語意。
- [ ] 視測試結果考慮把 Legacy Pose Gallery 與 Builder 合一，不作為 MVP 先決條件。— 暫緩：Phase 2 UI 尚未實機穩定。

---

## 9. 測試策略與驗收矩陣

### 9.1 Prompt 單元測試

- [ ] `hair=H001(long hair)`、`color=black hair` → `black hair, long hair`，沒有 `brown hair`。
- [ ] 只選 `H001`、不選髮色 → `long hair`，**不輸出**預設 `brown hair`。
- [ ] 選 Pose 時 Final Prompt 不含任何 Pose tags（MVP）。
- [ ] 髮色從 `black hair` 改 `blonde hair` → 舊色消失，服裝的 `black skirt` 不受影響。
- [ ] `blunt bangs`、`brown eyes`、`tareme` 可正確疊加，空欄位忽略。
- [ ] CharaDB O001 的 `blue sailor collar`、`red neckerchief`、`pleated skirt` 不被拆成個別單字。
- [ ] 已帶逗號的 tags 不重複插入逗號；無逗號的 `white sneakers` 保持詞組完整。
- [ ] 空字串／Null／缺欄位／未知分類／重複 tag 不產生例外。
- [ ] 全英文標籤 `, ` 正規化；不會變成 `,,`、多餘空白或末尾逗號。
- [ ] Append 到 `1girl, <lora:xxx:1>` 後，LoRA 字串和原有權重原封不動。

### 9.2 資料與授權測試

- [ ] CharaDB 索引缺少時，既有 Anhao Pose 正常可用。
- [ ] 重複的 CharaDB 440（官方 vs Anhao 漢化索引）不會變成 880 筆獨立素材。
- [ ] Anime Costume metadata 缺失／上游無回應，不拖慢啟動也不造成所有分頁失效。
- [ ] 遠端路徑無法偽裝本機路徑；禁止 `../` 路徑逃逸。
- [ ] Git diff / release package 不含來源網站圖片、zip 素材包、未授權圖片索引快取或任何 API 金鑰。
- [ ] Safe Mode 切換後，不能仍送出原本被隱藏的成人類別素材。

### 9.3 Forge / ControlNet 實機測試

| 情境 | 預期結果 |
|---|---|
| Normal Pose → Send Pose | Unit 1 有圖、enabled=true、Preprocessor=`canny`（以 Neo Dropdown 實際名稱為準） |
| ControlNet 傳送失敗（例如圖片不存在） | `.success()` 阻止後續 Append，txt2img Prompt 不變 |
| Depth Pose → Send Pose | Unit 1 有 Depth 圖、Preprocessor=`None` |
| 已有角色 LoRA 的 txt2img → Append | 原文字與 `<lora:...>` 完整保留，只新增片段 |
| 按 Send All | 同時收到正確 Pose 圖與 Final Prompt，沒有重覆附加 |
| 未安裝 ControlNet | Builder 仍能 Copy / Append，其他送圖操作清楚說明不可用 |
| 未選 Pose | Send All 禁用或提示，不能回報傳送成功 |
| 來源檔案或圖片不存在 | 顯示明確錯誤，不能悄悄使用前一次的圖 |
| 換 Outfit Provider | 新服裝 tags 覆蓋 Builder 當次的 Outfit 選擇，不改寫 txt2img 已有內容 |
| 切換 Normal/Depth 後 Send All | 使用當下最新圖片與對應 Preprocessor，不能使用 stale selection |
| Legacy Gallery / img2img | 舊功能沒有回歸或按鈕失效 |

**注意：** 純函式測試通過不等於 Forge 實機驗證通過。實作者需提供 Neo commit SHA、測試方式與已知差異。

---

## 10. 建議交付物與完成定義（Definition of Done）

每個 PR 至少包含：

1. 變更摘要與檔案清單、來源／授權注意事項。
2. 可讀的新增模組與原始介面的最小必要修改。
3. Prompt / Adapter 單元測試，以及適用的 Forge UI smoke test 指南。
4. README（資料來源手動安裝、路徑設定、啟用／停用、來源使用條款）。
5. 可重現的驗收截圖或文字結果：**選 CharaDB 髮型 + 眼睛 + 服裝 + Anhao Pose，Send All 後 txt2img 的 Prompt 和 ControlNet 1 的輸入都有更新。**
6. 不提交 `vendor/`、任何第三方圖片、整站鏡像或憑證；沒有沒必要的新依賴。

### 最終主要功能判定

- [ ] 一個介面可獨立選髮型／眼睛／服裝／Pose。
- [ ] 服裝可在 CharaDB 與 Anhao（Legacy）來源之間切換；Anime Costume 為 Phase 3 非阻塞擴充，不列入必要條件。
- [ ] CharaDB 使用原生 Danbooru tags；其他來源 Prompt 有保守格式化並可編輯。
- [ ] 未選髮色時不輸出髮色；MVP 不輸出 Pose tags。
- [ ] Send All 以 `.success()` 串接，ControlNet 失敗時不追加 Prompt。
- [ ] Pose 圖可確實送到 `txt2img` 的 ControlNet Unit 1。
- [ ] Final Prompt 可一鍵追加而不破壞既有角色／LoRA 設定。
- [ ] Depth / Canny 行為正確、失敗可理解、未安裝其他資料源也能正常操作。
- [ ] 程式碼開源部分與第三方圖片素材維持權利隔離。

---

## 11. 來源與待確認事項

### 確認過的來源

- 專案本體：https://github.com/Horlicks-p/forge-character-builder
- P0 PR #1（已合併）：https://github.com/Horlicks-p/forge-character-builder/pull/1
- Phase 1 PR #2（已合併）：https://github.com/Horlicks-p/forge-character-builder/pull/2
- Phase 2 PR #3（Draft，待實機驗收）：https://github.com/Horlicks-p/forge-character-builder/pull/3
- Phase 4A PR #4（Draft，stacked on #3，待實機驗收）：https://github.com/Horlicks-p/forge-character-builder/pull/4
- CharaDB 原始專案與 JSON：https://github.com/taki-ttt/charadb ／ https://github.com/taki-ttt/charadb/blob/main/data/items_public.json
- CharaDB LICENSE：https://github.com/taki-ttt/charadb/blob/main/LICENSE
- CharaDB 官方 Release（選用圖片包）：https://github.com/taki-ttt/charadb/releases/tag/v1.0
- Anhao：https://github.com/anhao/ai-pose-gallery-zh
- Anhao 已漢化 CharaDB 的 440 筆衣服 metadata：https://github.com/anhao/ai-pose-gallery-zh/blob/main/data/costume-anime-zh.json
- AI Costume Anime Gallery：https://ai-costume-anime-gallery.pages.dev/
- Hongye 對 Anime Costume（1,320 筆）整合說明：https://github.com/hongye007/open-pose-costume
- Hongye 素材來源／條款記錄：https://github.com/hongye007/open-pose-costume/blob/main/SOURCES.md
- Forge Neo（目標環境）：https://github.com/Haoming02/sd-webui-forge-classic/tree/neo（審查快照 `7d6fd36`）
  - Unit 標籤：`extensions-builtin/sd_forge_controlnet/scripts/controlnet.py`（`ControlNet Unit {i + 1}`）
  - `ForgeCanvas.background`：`modules_forge/forge_canvas/canvas.py`
  - 擴充根目錄加入 `sys.path`：`modules/scripts.py`
  - Gradio 版本：`modules/launch_utils.py`（`gradio==4.40.0`）

### 已確認、不需再查

- Neo 的 `ControlNetUiGroup`／`ForgeCanvas` 結構、Unit 編號、`sys.path` 行為、Gradio 版本（見上）。

### 開始寫 Phase 3 前必須重新查核

- [ ] Anime Costume 官方網站當時的實際授權／批次抓圖限制（本文件的來源說明不取代官方條款）。
- [ ] 是否存在官方可直接重用的**純 metadata**、其授權與檔名／欄位／數量。
- [ ] 遠端縮圖允許的實際 URL 與載入行為（HTTP、CORS、防盜鏈）；**不假設**瀏覽器能取圖就允許重新打包。
- [x] CharaDB Release 解包後圖片的實際路徑與檔案命名（Phase 2 前）。

### 回家實機時確認

- [ ] 當時實際的 Neo commit SHA。
- [ ] ControlNet preprocessor Dropdown 中 `canny` 的實際名稱。

---

## 12. 可直接貼給 Claude Code / Codex 的開工 Prompt

> 請先閱讀本檔全部內容，再讀取 `Horlicks-p/forge-character-builder` 的最新 `main` 與開啟中的 PR（0.1 節列出 2026-11-03 的狀態，開工前需重新確認）。目標環境只有 Forge Neo（`Haoming02/sd-webui-forge-classic` 的 `neo` 分支、Gradio 4.40.0）。不要重寫外掛、不要下載或提交第三方圖片、不要直接改 main。請依 **Phase 0 → Phase 1 → Phase 2 →（非阻塞）Phase 3 → Phase 4** 的順序拆成小 PR。第一個目標是在根目錄 `character_builder_core/` 完成 CharaDB 資料 adapter、Danbooru Prompt Builder 與測試；第二個目標是在既有 Forge UI 完成「髮型／眼睛 + 服裝 + Anhao Pose → Send All（`.success()`）→ txt2img Prompt + ControlNet Unit 1」的 MVP；Anime Costume 只是可取消的擴充。髮型預設色不輸出，MVP 不輸出 Pose tags。請保留 `img2img`、Depth／Canny、原有 vendor 路徑、Safe Mode 與所有 LoRA／Prompt 內容；遇到任何素材授權或 URL／版本不確定的地方，採可降級設計、記錄限制，**不要猜測欄位後直接大量抓圖**。每個 PR 請附變更清單、測試結果、回歸檢查與人工驗收步驟。

---

**設計原則總結：** CharaDB 管「髮型／眼睛／服裝的 Danbooru Prompt」，Anhao 管「Pose 圖與 ControlNet」，Anime Costume 是可取消的服裝擴充，Forge Neo `txt2img` 管真正的生圖。這是擴充、不是取代現有 Forge。

**核心交付：** Forge Neo + CharaDB Character/Outfit + Anhao Pose → `.success()` → txt2img Prompt + ControlNet Unit 1。

---

## 修訂紀錄

- **v1.4（2026-11-03）** 新增 Phase 2B：Anhao 多視角姿勢分組與角度選擇器。原資料 1,439 筆視角紀錄，按 `(體系, 姿势编号)` 得 288 組姿勢（287 組五視角、1 組四視角）。Builder 與 Legacy 程式、單元測試已提交獨立分支；Gradio harness 與 Forge Neo 實機驗收尚待執行。
- **v1.3（2026-11-03）** 反映實際進度與審查修正：
  - 新增 0.1「目前 PR 狀態」：PR #1、#2 已合併；PR #3（Phase 2）與 PR #4（Phase 4A，stacked）為 Draft、待 Forge Neo 實機驗收。
  - Phase 0 已確認項目打勾（PR #1 合併、`vendor/` 已忽略）；Phase 1 標為已合併。
  - Phase 3 維持 Deferred；Phase 4A 範圍與暫緩項目說明。
  - 4.6 補充 stale selection 規則：Outfit 來源切換清除舊選擇、dirty Final Prompt 的清除提示、Legacy Gallery 送出時才解析圖片與 Preprocessor。

- **v1.2（2026-11-03）** 專案更名為 **Forge Character Builder**（repo `forge-character-builder`）：
  - 套件 `ai_pose_gallery_core` → `character_builder_core`；入口 `scripts/character_builder.py`；JS `javascript/character_builder.js`（`appendCharacterBuilderPrompt`）。
  - 分頁標題 `Character Builder`、tab id `forge-character-builder`、CSS 前綴 `fcb-`。
  - 環境變數 `ANHAO_GALLERY_ROOT`（舊名 `AI_POSE_GALLERY_ROOT` 保留為相容備援）。
  - 理由：功能已不只是 Pose 圖庫；並避免與資料來源 AIポーズギャラリー／ai-pose-gallery-zh 的名稱混淆。
- **v1.1（2026-11-03）** 依 Forge Neo 原始碼審查（快照 `7d6fd36`）及 Claude／Codex 交叉審查修訂：
  1. 目標環境限定為 Forge Neo，移除 Forge Classic 優先描述；實機驗收須記錄 Neo commit SHA。
  2. 共用模組改放擴充根目錄 `ai_pose_gallery_core/`（Neo 會將擴充根目錄加入 `sys.path`）。
  3. Send All（及 Legacy Send to ControlNet）改以 `.success()` 串接。
  4. 明確定義 Unit 1 為第一個 txt2img `ControlNetUiGroup`（Neo UI 即標示為 Unit 1）。
  5. 髮型的預設 `color` 不輸出。
  6. MVP 的 Pose tags 預設留空；小型對照表移至 Phase 4。
  7. 髮色／前髮／髮飾／眼色／瞳型改用 Dropdown。
  8. Phase 3 Anime Costume 降為非阻塞、可取消的擴充。
  9. 待確認事項更新：已確認的 Neo 結構移除，新增實機確認項目（commit SHA、`canny` 名稱）。
  10. UI 介面全英文；來源日文／中文名稱只留在 metadata。
- **v1.0（2026-11-03）** 初版。
