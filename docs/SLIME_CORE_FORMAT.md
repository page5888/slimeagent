# Slime Core Format — `.slime` v1

> 本文件描述 AI Slime 主人資料的可攜式檔案格式。
> 凡按此規格實作的 reader / writer，無須讀本專案 source code 即可
> 跟主人手上的 `.slime` 檔互通。
>
> 這份規格的**存在本身**就是 manifesto 第三守則「不突然消失」的
> 制度保證——只要規格公開，本專案的公司死了、本人退坑，主人手上
> 的史萊姆**仍然可以被別人還原**。
>
> 修改規格 = 違背承諾。任何破壞向後相容的更動必須走 `format_version`
> 演進，不能就地改 v1 的解讀。

---

## 1. 用途

`.slime` 是把 `~/.hermes/` 整顆目錄（主人的史萊姆全部狀態：進化、
對話、反思卡、常規、表情包、avatar、wallet、等等）**端到端加密**
打包成單一檔案。設計目標：

- **可備份**：上傳到任何雲端 / 寄信給自己 / 燒進 USB 都安全
- **可還原**：在任何同主人實作的 reader 上能解碼還原
- **可繼承**：主人逝後家屬持有通行碼即可繼承
- **公司不解鎖**：通行碼僅留主人手上，任何 hosting 方都看不見明文

---

## 2. 完整檔案結構

```
偏移      長度    類型           內容
─────────────────────────────────────────────────────────────
0         6       bytes          magic = b"SLIME\x00"
6         1       uint8          format_version = 1
7         1       uint8          reserved = 0
8         2       uint16 BE      header_len
10        N       UTF-8 bytes    header_json (JSON object, 長度 = header_len)
10+N      ...     bytes          ciphertext (見 §4)
```

固定前綴（前 10 byte）必為 ASCII 可辨識，方便檔案類型偵測。

### 2.1 magic

`b"SLIME\x00"`（6 byte）。第 6 個 byte 永遠是 `\x00`，作為視覺
分隔。

### 2.2 format_version

uint8。本規格描述 `format_version = 1`。reader 看到不認得的版本
**必須**拒絕讀取，不要嘗試「盡量解析」。

### 2.3 reserved

uint8，必為 `0`。為未來預留。reader 應檢查是否為 0；非 0 時可拒絕
或警告。

### 2.4 header_len

uint16 大端序。`header_json` 的位元組長度。最大 65535。

### 2.5 header_json

UTF-8 編碼、`{...}` JSON 物件。最少必含這幾個欄位：

```json
{
  "format_version": 1,
  "kdf": "scrypt",
  "kdf_params": {
    "n": 32768,
    "r": 8,
    "p": 1,
    "dklen": 32
  },
  "salt":  "<base64-encoded 16 bytes>",
  "nonce": "<base64-encoded 12 bytes>",
  "created_at": 1714435200,
  "creator": "sentinel"
}
```

| 欄位             | 必要 | 說明                                                                   |
|------------------|:---:|------------------------------------------------------------------------|
| `format_version` |  ✓   | 必須等於外層 byte 6 的版本（雙重保險）                                 |
| `kdf`            |  ✓   | 目前僅支援 `"scrypt"`                                                  |
| `kdf_params`     |  ✓   | scrypt 參數。reader 必須使用**這裡**的數值，不要假設預設值             |
| `salt`           |  ✓   | base64 標準編碼的 16 byte 隨機鹽                                       |
| `nonce`          |  ✓   | base64 標準編碼的 12 byte 隨機 nonce                                   |
| `created_at`     |     | unix epoch seconds，僅供顯示，不參與解密                                |
| `creator`        |     | 寫入端 app 識別字串，僅供顯示                                          |

未來版本可加欄位；reader 看到不認得的欄位**應忽略**而非報錯。

### 2.6 ciphertext

剩餘所有 byte。AES-256-GCM 密文，**結尾包含 16-byte tag**
（標準 pyca / OpenSSL `aead.encrypt` 行為）。

明文是 ZIP 檔（見 §5）。

---

## 3. 金鑰衍生（KDF）

`kdf = "scrypt"` 表示使用 [RFC 7914](https://datatracker.ietf.org/doc/html/rfc7914)
之 scrypt：

```
key = scrypt(
  password = passphrase.encode("utf-8"),
  salt     = <header.salt>,
  N        = <header.kdf_params.n>,
  r        = <header.kdf_params.r>,
  p        = <header.kdf_params.p>,
  dkLen    = <header.kdf_params.dklen>   # 必為 32 (AES-256)
)
```

實作建議：

- Python：`hashlib.scrypt(...)`（stdlib）
- Node：`crypto.scryptSync(...)`
- Rust：`scrypt` crate
- Go：`golang.org/x/crypto/scrypt`

---

## 4. 對稱加密

```
plaintext  = AES_256_GCM_decrypt(
  key        = <derived from §3>,
  nonce      = <header.nonce>,
  ciphertext = <body bytes>,
  aad        = ε   # empty / not used in v1
)
```

GCM tag 與密文未分離（pyca AESGCM convention）。如使用會分離的
函式庫，從密文末尾切出最後 16 byte 作為 tag。

**注意**：v1 不使用 AAD，但 reader 應傳空字串 / `null`，**不要**
傳 header bytes 當 AAD（將來改變這條會破壞向後相容）。

---

## 5. 明文 ZIP 內容

明文是標準 ZIP 檔（可用 `unzip -l` 之類工具直接列出檔名，前提是
你已正確解密拿到明文）。內容鏡射 `~/.hermes/` 的相對路徑：

```
aislime_evolution.json
sentinel_settings.json
chat.log
routines/<routine_id>.json
expressions/<expression_id>/...
avatar/<id>.png
audit/...
usage.jsonl
safety_crisis.jsonl
...
```

不保證每次匯出都包含全部清單；Slime 隨版本演進可能新增 / 棄用
個別檔案。**reader 應容忍未知檔名**，照樣解出來放進對應位置。

ZIP 路徑使用 forward slash，所有路徑必為相對路徑（不以 `/` 開
頭、不含 `..`）。違反時 reader **必須**拒絕還原。

---

## 6. 安全模型

- 通行碼**只**存在主人本機。本專案 / hosting 方 / 任何第三方
  在未知通行碼前，看到的是均勻隨機的 ciphertext。
- AES-GCM tag 失敗無法區分「通行碼錯」vs「檔案損壞」。這是設計，
  不是 bug——避免提供 oracle 給暴力破解。
- 通行碼遺失 = 該 `.slime` 永久不可還原。本專案無後門、無 escrow、
  無重置機制。這是 manifesto 第三守則的代價。

---

## 7. 版本演進規則

- v1 byte 結構**永遠**讀得回來。新版本只能往新 `format_version`
  分支，不能改 v1 解析。
- 新版本可在 `header_json` 加欄位，舊 reader 應忽略未知欄位。
- 棄用某個 KDF（例如 v2 換 Argon2id）必須同時：
  1. 在新版本的 `kdf` 欄位用新值
  2. 舊 reader 看到不認得的 `kdf` **必須**拒絕並提示使用者升級
     reader，**不要**靜默走別的 KDF。

---

## 8. 快速 reader 範例（Python ≥ 3.10）

```python
import base64, hashlib, json, zipfile, io
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

def read_slime(passphrase: str, path: str) -> dict[str, bytes]:
    data = open(path, "rb").read()
    assert data[:6] == b"SLIME\x00", "not a slime file"
    assert data[6] == 1, f"unsupported version {data[6]}"
    hlen = int.from_bytes(data[8:10], "big")
    header = json.loads(data[10:10+hlen])
    salt = base64.b64decode(header["salt"])
    nonce = base64.b64decode(header["nonce"])
    p = header["kdf_params"]
    key = hashlib.scrypt(
        passphrase.encode(), salt=salt,
        n=p["n"], r=p["r"], p=p["p"],
        dklen=p["dklen"], maxmem=256*1024*1024,
    )
    plaintext = AESGCM(key).decrypt(nonce, data[10+hlen:], None)
    return {
        name: zipfile.ZipFile(io.BytesIO(plaintext)).read(name)
        for name in zipfile.ZipFile(io.BytesIO(plaintext)).namelist()
    }
```

23 行 Python，無依賴本專案任何 module，是這份規格存在的意義
本身——**主人不需要我們**也能還原他的史萊姆。
