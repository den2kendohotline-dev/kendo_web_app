# 剣道部 試合結果管理Webアプリ

部員全員が同じURLから利用できる、剣道部向けの試合結果管理アプリです。

## 主な機能

- 部員ごとのログイン
- 管理者による部員アカウント作成
- 選手登録
- 団体戦と個人戦の入力
- PostgreSQLまたはSQLiteへの保存
- 全部員で試合履歴を共有
- OB・OG向け報告文の自動生成
- スマートフォン対応

## ローカルで起動

Python 3.11以上を使用します。

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS・Linux:

```bash
source .venv/bin/activate
```

依存関係をインストールします。

```bash
pip install -r requirements.txt
```

環境変数を設定して起動します。

Windows PowerShell:

```powershell
$env:SECRET_KEY="長いランダム文字列"
$env:ADMIN_USERNAME="admin"
$env:ADMIN_PASSWORD="初期管理者パスワード"
uvicorn main:app --reload
```

macOS・Linux:

```bash
export SECRET_KEY="長いランダム文字列"
export ADMIN_USERNAME="admin"
export ADMIN_PASSWORD="初期管理者パスワード"
uvicorn main:app --reload
```

ブラウザで `http://127.0.0.1:8000` を開きます。

## データベース

何も設定しない場合は、プロジェクト内の `kendo.db` に保存されます。

本番環境では、環境変数 `DATABASE_URL` にPostgreSQLの接続先を設定します。

## 公開

`render.yaml` を含めているため、GitHubへアップロードした後、RenderのBlueprintからWebサービスとPostgreSQLを作成できます。

公開時には、必ず次を設定してください。

- `ADMIN_PASSWORD`
- `SECRET_KEY`
- `DATABASE_URL`

初回ログイン後、管理者メニューの「部員アカウント」から各部員のアカウントを作成します。

## 注意

初期管理者のパスワードを変更する画面は、まだ未実装です。公開前に環境変数 `ADMIN_PASSWORD` を十分に長いものへ変更してください。
