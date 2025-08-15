Happy Case-Shiller day! The last Tuesday of the month is always an important one for the housing market: two of the key indices, the S&P CoreLogic Case-Shiller home price index and the FHFA home price index were released this morning. These are the gold-standard measures of home prices because they are based on repeat sales.

So what did today's data show us?

#### At first glance, it looks like a healthy market...

This morning's data showed a continued rebound in home prices. The Case-Shiller index rose by xx from a year earlier, while the FHFA index rose by yy (fn: NSA). This is close to the 5% rate of annual home price appreciation we have seen over the past 50 years.

- *home prices, y/y change*
#### ...but this is a statistical quirk

In fact, the y/y figure disguises much softer home price appreciation than usual. Here's why: after starting to turn down in June 2022, home prices bottomed in January 2023—exactly one year prior to the data released this morning. As a result, the year-over-year comparison looks unrealistically solid.
#### A more accurate read: 3m change, vs history

I prefer to look at price changes in the non-seasonally adjusted data over the last 3 months, compared to prior years over the same three months (I may sound like a broken record, since I applied the same metric to existing home sales last week—but hey, it's a good record). I prefer the non-seasonally adjusted data because the seasonal corrections are questionable. And the 3m changes vs history give us a much more accurate read on the state of the market, for 3 reasons.

*seasonal adjustment adopted by statistical agencies is sometimes seen as a potentially dangerous procedure that may compromise the intrinsic properties of the original series. In fact, there is always some loss of information from seasonal adjustment, even when the seasonal adjustment process is properly conducted.* https://www.imf.org/external/pubs/ft/qna/pdf/2017/chapterv7.pdf

First, this measurement solves a big problem with y/y changes: a year is a long time. Home prices may be higher than back then, but that tells us little about what's happened in the interim, and in which direction, and how quickly, things are moving these days. The "comps" from 3 months ago are less flattering, and give us a closer to "real-time" gauge on the direction of prices.

Second, the 3m change vs history measure allows us to gauge home price appreciation in a timely way without picking up too much noise. Prices bounce around from month to month, so by looking at them over a slightly longer horizon, we smooth these bumps out.

Third, it solves the seasonality problem. Seasonal series cannot be measured on a monthly basis. But measuring prices on a 3m basis, and then comparing them to history over that same period, neutralizes the seasonality: we're making a like-for-like comparison.

By this metric, the housing market appears to be entering the busy spring season on a weaker footing than usual.

- *3m change, vs history*




For next time: the three flavors of home price indices, and why Zillow is my fave, and what the latest data tells us (on zillow release day)






Key points:

-Usefulness is a function of: 
- timeliness and frequency (early and often)
- accuracy (measures what you want it to)
- low revisions
- access (free, ideally in API, etc)
- methodological soundness
- easy to understand

There are three kinds of indices:

1. Median sales (NAR, Census): Reflects different homes over time. In the case of Census, highly delayed, and infrequent (only quarterly). Some benefits, like can match it to other census data (eg—which demographic is seeing their homes go up the most). NAR's data locked up behind a paywall and very expensive. 
2. Repeat sales (Case-Shiller, FHFA): Reflects the same homes, but may still reflect changing composition of sales. Frequent (monthly, and FHFA releases more granular data quarterly). Easy to access.
3. Autoregressive sales indices (Zillow): Uses large data sets to tease out the time element of sales prices, controlling for all other factors. Monthly. Very easy to access, including API. More timely than others (released earlier). May pickup changes more slowly.

What do we want from an index?

It should be representative of what we are trying to measure: broadly, American housing. It should be released frequently, and with a small lag from what it's measuring. It should not be subject to frequent revisions. It should be easy to access (free), and easy to understand. It should not be overly volatile. It should reflect changes in market direction early.












This morning S&P Global released the latest reading of the S&P CoreLogic Case-Shiller home price index. 




Comparison metrics:
- Frequency
- Geographic granularity
- Scope of housing included
- History
- Timeliness (time of transactions)
- Release dates


Other sources:
Faster capital brief description of the various indices [here](https://fastercapital.com/content/Comparative-analysis--SP-Case-Shiller-US-NHPI-vs--other-housing-indices.html)
Academic review of indices by Penn, recommends autoregressive indices [here](https://faculty.wharton.upenn.edu/wp-content/uploads/2013/05/Brown_2013_House_Price_Index_Methodology_1.pdf)
Another good Penn paper [here](https://realestate.wharton.upenn.edu/wp-content/uploads/2017/03/724.pdf)
SeekingAlpha: comparison of CS and FHFA [here](https://seekingalpha.com/article/4208292-fhfa-and-case-shiller-home-price-indices-difference)
Chicago Fed describing the broad types of house indices [here](https://www.chicagofed.org/publications/profitwise-news-and-views/2018/determinants-of-housing-values-and-variations-in-home-prices-across-neighborhoods-in-cook-county)
Redfin announcement, comparison to CS index [here](https://www.redfin.com/news/redfin-home-price-index/)


## What's an index?

An index provides a single number to represent the value of many different observations. For example:
- the UN's Human Development Index measures each country's life expectancy, education, and per capita income indicators. The index score for the United States is 0.93.
- The consumer price index (CPI) collapses the prices of all the goods and services the typical American household buys into a single number. At the latest reading, the CPI for the US is 311.
- The S&P 500 index represents the price of the largest American companies. Today's the S&P trades near 5,200.

## Is an index just an average?

In many cases, an index is merely an average of all the observations. But more often than not, there is some special sauce—a specific weighting of the observations, for example—than makes an index something slightly different than an average. Still, for simplicity, thinking of an index as an average is not a bad approximation.

## Home price indices

Home prices across the United States are measured by various indices (fn: existing home sales only). The most commonly-cited indices are provided by:

- S&P/Case-Shiller (hereafter, "Case-Shiller"), see [[Notes on CS methodology]]
- Federal Home Loan Housing Association (FHFA), see [[FHFA vs Case Shiller index methodology]]
- National Association of Realtors (NAR)
- Zillow (how do hedonic indices work, from GPT, [here](https://chat.openai.com/share/957692e8-0c17-422e-a585-666ee19ac1a0))
- CoreLogic
- Redfin
- Black Knight 

## Plural: indexes or indices?

The plural of index is indexes or indices. I come from a finance background, where everyone talks about indices. Outside of finance and academia, and especially in the US, many people say indexes. Either is perfectly fine.

## Why do we need them?

Collapsing many values—whether it's development indicators, prices of goods and services, or stocks—is essential for understanding changes over time, or making comparison across entities. For example, the US score of 0.93 on the HDI index is 6% higher than it was 30 years ago, and positions the country at number 20 out of 193 countries. The S&P-Case Shiller national index is 310.67—a 0.4% drop from the prior month. 

Whenever the thing we are talking has many pieces—the stock market (comprised of many individual stocks), the price level in the economy (comprised of all the things households buy), or real estate (comprised of all the different kinds of homes across the country)——we need an index to summarize these pieces. Only with an index can we make historical and cross-wise comparisons.

## Real estate indices: pros and cons of each

In order to simplify something multitudinous into a single number, each index is constructed using a specific methodology: what goes into the index, how they are combined and simplified, and how frequently the observations are made and the index is produced. 

Because indices are necessarily simplifications, they involve tradeoffs. Each index is unique in the tradeoffs it makes. What makes one index more useful for one type of user may also make it irrelevant for another.

Representativeness: 

[[FHFA vs Case Shiller index methodology]]

Drawbacks of indices that are not repeat sales [[Penn paper on price indices.pdf#page=2&selection=12,0,25,80|Penn paper on price indices, page 2]]
Criticism of repeat sales indices excluding new homes [[Penn paper on price indices.pdf#page=2&selection=40,0,49,89|Penn paper on price indices, page 2]]
CS accounts for heteroskedasticity [[Penn paper on price indices.pdf#page=4&selection=3,0,16,38|Penn paper on price indices, page 4]]
They eliminate so many observations that they can't estimate an index for small areas[[Penn paper on price indices.pdf#page=5&selection=31,0,37,66|Penn paper on price indices, page 5]]
They have to be updated retroactively [[Penn paper on price indices.pdf#page=6&selection=93,3,93,45|Penn paper on price indices, page 6]]
Well said: "In any time span, houses can be categorized as follows: new home sales, repeat sales with no changes in the house, repeat sales homes with changes, and houses not sold. Repeat sales methods only use data in the second category"

CS vs FHFA (seeking alpha):
Additionally, FHFA weights all homes equally. Case-Shiller is value-weighted, meaning price trends for higher valued homes tend to have more of an impact on the index.
The way in which data is collected and organized is the other major differentiating factor. S&P CoreLogic uses local government assessor and records offices to get the valuation of homes. FHFA, on the other hand, uses the value of mortgages bought by either Fannie Mae or Freddie Mac. This removes refinancing appraisals that are picked up in the Case-Shiller data. Unfortunately, it fails to include a variety of mortgages, such as subprime and VA loans. The FHFA breaks out state and regional prices with a lot more granularity; unlike Case-Shiller, it offers versions for census regions and states, while the Case-Shiller version only offers 20 metros and a national composite.

The discussion below, regarding the benefits and limitations of various housing indices, 
Here are some of the major criteria:

#### Composition: What is included in the index? 
The S&P 500 includes only the largest 500 companies. The FHFA home prices index includes only those homes that were financed with a government agency-sponsored mortgage.

#### Weighting: How much will each observation matter, compared to the others?
The S&P 500 gives a greater important to the stock prices of larger companies, as measured by their market capitalization (the product of the prices of their shares and the number of shares outstanding). By contrast, the Dow Jones Industrial Average weights each company equally.

#### Timing: How frequently to measure
The S&P 500 is measured every fraction of a second. The CPI is measured once a month. The CoreLogic index is measured weekly.

There are other considerations aside from composition, weighting, and timing, but these are the most important ones.

### Drawbacks of indices, generically

In the process of simplifying something multitudinous, indices make compromises. These compromises may result in some drawbacks.

##### Weighting
An index may overemphasize some of the elements at the expense of others. For example, the Case-Shiller index is value-weighted: it ascribes more weight to more expensive homes. At times (like the present) when higher priced homes are rising in price more rapidly than lower-priced homes, Case-Shiller may become less representative of homes across the price spectrum.

- Representativeness: Many indices collapse a limited set of observations, rather than sampling the entire universe. In the process, the index risks portraying something other than what it intends to. Take the consumer price index, for example: it aims to represent the prices paid for a representative basket of goods and services that the median American household consumes every month. But observing every single price in the economy is not possible. 
- Qualit




**There are 3 different kinds of indices**


**The major differences between the major indices**


**A comprehensive comparison table**

