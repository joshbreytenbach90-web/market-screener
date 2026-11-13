// Power BI > Home > Transform data > New Source > Blank Query > Advanced Editor > paste.
// Name the query: MarketScreen
// Replace YOUR-USERNAME with your GitHub username (and the repo name if you used a different one).
let
    Url      = "https://raw.githubusercontent.com/YOUR-USERNAME/market-screener/main/output/market_screen.csv",
    Source   = Csv.Document(Web.Contents(Url),
                 [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed    = Table.TransformColumnTypes(Promoted, {
        {"RunTimestamp", type datetime}, {"Market", type text}, {"Category", type text},
        {"CategoryOrder", Int64.Type}, {"Rank", Int64.Type}, {"Ticker", type text},
        {"Name", type text}, {"Display", type text}, {"Sector", type text}, {"Currency", type text},
        {"Price", type number}, {"MarketCap", type number}, {"MarketCapUSD", type number},
        {"PE", type number}, {"ForwardPE", type number}, {"PB", type number}, {"PEG", type number},
        {"DividendYield", Percentage.Type}, {"FCFYield", Percentage.Type}, {"ROE", Percentage.Type},
        {"EarningsGrowth", Percentage.Type}, {"RevenueGrowth", Percentage.Type},
        {"Return1M", Percentage.Type}, {"Return3M", Percentage.Type}, {"Return1Y", Percentage.Type},
        {"AnalystUpside", Percentage.Type}, {"Recommendation", type text},
        {"Score", type number}, {"Reason", type text}}, "en-US")
in
    Typed
