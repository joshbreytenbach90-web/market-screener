# Market Screener – GitHub + Power BI setup (no installs needed)

GitHub runs the screener for free every morning at about 05:30 SA time. It saves
`output/market_screen.csv`, and Power BI reads that file from a web link.

## Part 1 – Set up GitHub (about 10 minutes, in your browser)

1. Go to **github.com**, click **Sign up** and create a free account. A personal email is fine.
2. Click **+** (top right) → **New repository**.
   - Repository name: `market-screener`
   - Choose **Public**. Power BI can then read the file without a password. It only contains public market data.
   - Tick **Add a README file**, then click **Create repository**.
3. Upload the files. Click **Add file → Upload files**, then drag in:
   - `market_screener.py`
   - `requirements.txt`
   - the `powerbi` folder

   Then click **Commit changes**.
4. Add the daily schedule. Click **Add file → Create new file**.
   - In the name box, type exactly: `.github/workflows/daily-screen.yml`
     The slashes create the folders automatically.
   - Open `daily-screen.yml` from the zip in Notepad, copy all of it and paste it into the big box.
   - Click **Commit changes**.
5. Run it once now. Open the **Actions** tab and click **Daily market screen** → **Run workflow** → **Run workflow**.
   - It takes about 10–15 minutes. A green tick means it worked.
   - You'll then see a new `output` folder in your repository containing `market_screen.csv`.

## Part 2 – Connect Power BI (about 5 minutes)

1. In your repository, open `output` → `market_screen.csv`, then click **Raw**.
   Copy the web address. It looks like:
   `https://raw.githubusercontent.com/YOUR-USERNAME/market-screener/main/output/market_screen.csv`
2. In Power BI Desktop, go to **Home → Transform data → New Source → Blank Query → Advanced Editor**.
   - Paste the contents of `powerbi/PowerQuery_GitHub.m`.
   - Replace the address in the `Url =` line with the one you copied.
   - Click **Done** and rename the query to **MarketScreen**.
3. If Power BI asks how to connect, choose **Anonymous** → **Connect**.
4. Click **Close & Apply**. Then build the visuals:
   - A **Matrix** with Rows = `Rank`, Columns = `Category` and Values = `Display`
   - A **Slicer** on `Market`

## Part 3 – Make the report refresh daily

1. In Power BI Desktop, go to **File → Publish** and choose your workspace.
2. In Power BI Service (app.powerbi.com):
   1. Open the workspace and find the semantic model. Click **⋯ → Settings**.
   2. Under **Data source credentials**, click **Edit credentials**, choose **Anonymous**, and click **Sign in**.
   3. Under **Scheduled refresh**, switch it **On**. Choose **Daily**, time **07:30**, and time zone **(UTC+02:00) Harare, Pretoria**.

You don't need a gateway, and your laptop can be off.

## If something goes wrong

- **Red ✗ in Actions:** click the failed run to see the error.
  - The most common cause is Yahoo temporarily blocking GitHub's servers. Click **Re-run jobs** later.
  - If it keeps failing, lower `WORKERS = 3` to `WORKERS = 1` in `market_screener.py`. Open the file on GitHub and click the ✏️ pencil to edit it.
- **The run starts later than 05:30:** GitHub sometimes delays scheduled runs at busy times. Refreshing Power BI at 07:30 leaves room for that.
- **Power BI can't connect, or shows a "Formula.Firewall" or privacy error:** go to File → Options → Privacy and choose "Ignore the Privacy Levels". In Service, set the credentials to Anonymous with privacy level Public.
- **Company policy:** your company's Power BI admin may block web data sources. If refresh fails with a policy or permission message, ask IT.

Market data comes from Yahoo Finance (free and delayed). This is a quantitative screen, not investment advice.
