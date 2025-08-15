##### Case-Shiller, FHFA, Zillow, the list goes on. But one of these is better than the rest.
#### Introduction

*This note should be useful for anyone who needs to track home prices, or who needs to communicate home price data to others. I stick to the basics here. For those who want to get into the weeds, I provide links to conversations with ChatGPT in which I ask the robot to explain complicated things to me in plain English. If you only have 30 seconds to read this, skip straight to figure 2.*

No single real estate number is more closely watched than the Case-Shiller index, which tracks the prices of single-family homes bought and sold. The focus on Case-Shiller and other home price indexes is deserved. While we can easily observe market prices in real time on the MLS and listing apps like Zillow, only indexes allow us to make observations about the market as a whole, and to make comparisons over time and across markets.

Maybe most importantly, indexes tell us which regime we are in: are prices rising or falling? This is crucial information. To take only one obvious example, a prospective homeseller perceiving a change from rising to falling prices will want to reduce their asking price, making a faster sale to avoid selling at an even lower price in the future. 

But Case-Shiller and other indexes are based on transactions that happened months earlier. As a result, they are often criticized as a poor proxy for current market conditions. Many think the information is degraded at best, and maybe even entirely useless. But this criticism is overcooked.

Yes, index data is delayed. But in the real estate market, prices exhibit momentum. That is, they tend to move in the same direction for long stretches of time. If prices went up last month, it's much more likely than not they are currently rising, too.

Still, the criticism has merit. For those participating in transactions today, the freshest data is much more useful than stale data from months ago. Those in the business of buying and selling homes should therefore be laser-focused on the most timely data.  In particular, what matters most is seeing the turning points as early as possible—it's these turning points that necessitate a change in our approach to market.
#### One signals turning points earlier than others

There are half a dozen widely-followed home price indexes. Does it matter which you use? 

At first glance, the chart below suggests the major indexes all say the same thing. For simplicity, I have only included 3 major indexes—Case-Shiller, FHFA, and Zillow—but adding more wouldn't change the picture: they all move together. 

---> *Chart: 3 indexes over time—dots and lines—seem to show same thing*

Since the indexes move together, maybe it doesn't matter which you follow. But that couldn't be further from the truth. In fact, there is one index that is far superior to the others: it reliably signals turning point in the market 1-2 months before others. In this note for paying subscribers, I dig into:

- Index 101: the tradeoffs of simplifying the market into a single number
- How to communicate index data to non-experts
- Why one index is far better than all the others

By subscribing, you'll get access to this article, our customizable Buy or Rent tool, and the full archive of Home Economics articles where I've written about everything from where mortgage rates are going to why buying makes more sense than renting. 

----paywall----
#### Index 101

An index provides a single number to represent the value of many different observations. For example:
- the UN's Human Development Index measures each country's life expectancy, education, and per capita income indicators. The index score for the United States is 0.93.
- The consumer price index (CPI) collapses the prices of all the goods and services the typical American household buys into a single number. At the latest reading, the CPI for the US is 311.
- The S&P 500 index represents the price of the largest American companies. Today's the S&P trades near 5,200.

#### Is an index just an average?

In many cases, an index is merely an average of all the observations. But more often than not, there is some special sauce—a specific weighting of the observations, for example—that makes an index something slightly different than an average. Still, for simplicity, thinking of an index as an average is not a bad approximation.
#### Why do we need indexes?

Whenever the thing we are talking has many pieces—the stock market (comprised of many individual stocks), the price level in the economy (comprised of all the things households buy), or real estate (comprised of all the different kinds of homes across the country)——we need an index to summarize these pieces. Only with an index can we make historical and cross-wise comparisons. 

For example, how can we compare the quality of life of American to those in other countries? We could talk about healthcare, infrastructure, rule of law, ease of doing business, and dozens of other metrics. This would take a long time, and probably confuse us. Instead, we might use an index: the US score of 0.93 on the Human Development Index is 6% higher than it was 30 years ago, and positions the country at number 20 out of 193 countries. 
#### There are many indexes, but Zillow's is the best

Home prices across the United States are measured by various indexes (fn: existing home sales only). The most commonly-cited indexes are provided by:

- S&P/Case-Shiller (hereafter, "Case-Shiller"), see [[Notes on CS methodology]]
- Federal Home Loan Housing Association (FHFA), see [[FHFA vs Case Shiller index methodology]]
- National Association of Realtors (NAR)
- Zillow (how do hedonic indexes work, from GPT, [here](https://chat.openai.com/share/957692e8-0c17-422e-a585-666ee19ac1a0))
- CoreLogic
- Redfin
- Black Knight 

Zillow's Home Value Index (ZHVI) is superior to the others, for 3 reasons.
#### Reason 1: Zillow's index is constructed in a really smart way

There are three kinds of indexes. They differ from each other in a number of ways, but most importantly in terms of what they include. I label them "Bronze", "Silver", and "Gold" to denote their quality.
###### Bronze: Median price indexes

These types of indexes take the median value of all the homes sold in period, in one place. The National Association of Realtor's (NAR) index works this way. It's not remotely accurate. 

NAR's index doesn't account for a changing mix of homes. When smaller homes trade more than larger ones, their index will be biased downwards. That happened at the end of 2023, when NAR reported a +3% gain in home prices, while other indexes reported roughly doubly that. The NAR index also includes new homes. Because new homes tend to be more expensive than older homes, NAR's index is biased upwards. On top of all of that, it's only released every quarter. I'm not sure who the target user for the NAR index is—maybe nobody?
###### Silver: Repeat-sales indexes

These are the most widely-cited indexes. S&P CoreLogic Case-Shiller and the FHFA's index are examples of repeat sales indexes. They're are a big improvement over median sales price indexes, because they measure the change in price on the same home over time. It's a completely apples-to-apples gauge of the market. 

These indexes also do some clever thing to boost accuracy, like more heavily weighting more recent transactions (they do this to account for the model's heteroskedasticity—see my dialogue with ChatGPT where the robot explains heteroskedasticity to me like I'm 15 years old, [here](https://chat.openai.com/share/9fb40641-32e4-4cba-8421-a8bad73b83c5))

But these indexes have some enormous drawbacks. In fact, the drawbacks are so large, it's really pretty surprising that the industry uses these types of indexes at all. 

Consider this: less than half of one percent of the housing stock transacts every month. From this already small universe, the Case-Shiller index kicks out all but those that have transacted before. Unsurprisingly, what you're left with is transactions on a tiny fraction of all homes. 

Researchers at Penn make this case elegantly: "In any time span, houses can be categorized as follows: new home sales, repeat sales with no changes in the house, repeat sales homes with changes, and houses not sold. Repeat sales methods only use data in the second category".

When you are measuring such a small sliver of the market, you run into some major problems (I discuss them in the next section).

###### Gold: Hedonic indexes

Hedonic indexes like Zillow's Home price index (the ZHVI) are not based on home transactions (and certainly not only on the tiny world of those that are repeated transactions). They calculate the price change on the entire stock of homes. 

But do they know the change in the value of all homes, if only a handful have been bought and sold?

Zillow estimates the value of all homes, all the time—what they call a "Zestimate". It's based on matching the characteristics of each home— sales transactions, tax assessments and public records, in addition to home details such as square footage and location, and other parameters—to homes that actually sold. 

This is of course tricky, since as any realtor worth their salt will tell you, each home is unique. But consider this: Spotify can generate uncannily excellent music playlists for me. This is both neat and a bit annoying. My taste in music is unique! I like Brian Eno but also Taylor Swift. Still, my uniqueness is no match for a robot armed with enough data. My taste in music can be predicted pretty accurately. So can the value of my home.

Click [here](https://chat.openai.com/share/957692e8-0c17-422e-a585-666ee19ac1a0) for a conversation with ChatGPT about the technical aspects of Zillow's index.

These Zestimates have become more precise over time, as both the state of technology and Zillow's use of it have improved. Zillow smartly crowdsources intelligence to make their models better. For example, in 2019 the company ran a competition amongst data scientists to improve their model. 3,800 teams from 91 countries competed to win $1 million. A data scientist from North Carolina won, and Zillow incorporated his methodology into their Zestimates. 

In particular, Zillow has recently added newer, more sophisticated modeling techniques ("neural estimates") which allow their models to train themselves on the data, learning about what works and what doesn't over time (FN: the "neural zestimate" methodology [here](https://www.zillow.com/research/methodology-neural-zhvi-32128/)). As a result of this constant tinkering, Zillow's home pricing algorithms have become much more accurate—so much that now Zillow's index, as of January 2023, is priced off these Zestimates.

When Zillow reports it's home price index, it's based on every single home in it's database—over 100 million of them (sourced from county public records offices, MLSs, brokerages, real-estate agents, and individual households across the country). And even if you still have doubts about whether Zillow can accurately price each individual home, we know that, in the aggregate, it clearly can: Zillow's index closely tracks the most widely used indexes.

This begs the question: if Zillow's index just mirrors the others, what's it's value? The answer is two-fold: granularity and timeliness.

#### Reason 2: Granularity

The Case-Shiller index kicks out so many transactions, and is left with so few, that in order to build up a sample size of enough transactions they can only report their index at the national level and for the 20 largest MSAs. 

By contrast, Zillow reports price changes down to the zip code. It's able to do this because—even by only taking the "trimmed mean" (middle third) of transactions, which smoothes out any kinks are removes outliers, it still ends up with sufficient transactions to report a price index down to the neighborhood level.

*---> national map of price changes at Zip-code level*

#### Reason 3: Timeliness

Repeat sales indexes like Case-Shiller and FHFA have sample sizes that are too small to report sales month-by-month. Instead, they average the prices of transactions over 3 months. Moreover, they have some severe lags because of the way they collect data.

Case-Shiller uses local government assessor and records offices to get the valuation of homes. There are long lags from transaction time to reporting (not to mention the lag between signing and closing). 

The FHFA's index is based on mortgages bought by either Fannie Mae or Freddie Mac. Since there is a 30- to 45-day lag from loan origination to enterprise funding and additional data processing time, FHFA receives data on new originations with a two-month delay. 

As a result, these indexes don't report prices for any given month until 2 months later (the Case-Shiller and FHFA indexes last week reported on transactions that took place in January!), and even then, they report an average of prices over 3 months—not the latest month.

Simply put, that means that the number we just got last week (at the end of March) from Case-Shiller and the FHFA included home transaction signing as far back as September!

By contrast, Zillow is pricing homes in real time. They report home prices only three weeks after the close of the month. Today's data from Zillow covers February—the same figures that Case-Shiller/FHFA will only report at the end of May, almost two months from now.

**The most important implication of all of this is that Zillow tells you where to market is going weeks, if not months, before more widely-followed indexes like Case-Shiller.** My analysis of the two indexes suggests that, on average, the Zillow index signals a turn in market direction 6 weeks before Case-Shiller.

What does the latest data say? Even though home prices are entering the spring with weaker momentum, the latest data suggests the market is picking up steam. My bet is that the other measures of home prices, like Case-Shiller, will tell that same story over the coming months.

*chart: barcode plot of monthly second derivative—should show Zillow changing before others*

#### Overflow


another set is better but still pretty flawed (indexes from S&P CoreLogic Case-Shiller, and FHFA), and then there is the Zillow index. 


- 




1. A smarter methodology: modeled prices

The advantages of the Zillow index over competitors derives from the way it's constructed. Unlike the NAR index, for example, which takes the median price of all homes sold, or the Case-Shiller index, which tracks only repeat sales, the Zillow index is based on a model: this method combines data on sales price with property and location characteristics, and controls for factors that might affect sales price. A hedonic model reveals how much influence individual factors have on sale prices, and, by isolating the effects of those variables, allows for the development of an index tracking price changes over a period of time on properties with similar characteristics. If this all sounds like jibberish, I got ChatGPT to explain it to me in simple terms—you can see our conversation here. If you want to understand why Zillow's index is built in a smarter way than the others, but have the patience or interest for that to take less than 2 mins, I highly recommend you check it out.

To be honest, Zillow's index adds little in terms of direction or momentum to existing indexes. As Zillow points out, "Since 2014, Zillow’s monthly forecast of the Case-Shiller U.S. National Home Price Index has been within 0.25 percentage points of published data roughly 96 percent of the time." But, in addition to the points below, this is an advantage: it delivers the same thing, just more granularly and in a more timely way. "With few exceptions, movements in ZHVI are generally echoed by Case-Shiller data released more than a month later."

Mechanically we do that by taking an aggressively trimmed-mean (middle third) of Zestimates and chaining back with a repeat-Zestimate index. Ever heard of a repeat sales index? Like that, but instead of the same property finally selling again to make a matched pair of home prices the model can use, the ZHVI synthesizes changing neural Zestimates on all individual properties each month, providing insight in neighborhoods and housing segments where other methods fail for lacking enough of the “right kind” of data.

Zillow neural zestimate to power indexes started Jan 2023. Importantly, the [way the Zillow Home Value Index](https://www.zillow.com/research/zhvi-methodology-2019-deep-26226/) is calculated has not changed, just the Zestimates used to construct it. (two advantages: each home estimate, index methodology)+ 1. Comprehensiveness: ZHVI draws on Zestimates calculated on more than 100 million U.S. homes, including new construction homes and/or homes that have not been listed for sale in many years. This offers a fuller picture than indexes that rely solely on data recorded only on those homes that sell in a given period.

To create the [Neural Zestimate](https://zillow.mediaroom.com/2021-06-15-Zillow-Launches-New-Neural-Zestimate,-Yielding-Major-Accuracy-Gains), we train a neural network model with long and detailed histories of transactions, listings, and property information.  All of this rich, historical data is sourced from our Zillow Database of All Homes – a future-enabling collaboration with county public records offices, MLSs, brokerages, real-estate agents, and individual households across the country. The same information you can comb through on our home pages is put to work for consumers and analysts seeking to understand the price of homes and increasingly volatile housing markets.

Some of the most famous home price measures that do control for the changing mix of homes that sell – like the S&P CoreLogic Case-Shiller Home Price Index – are best suited when measuring real estate as a trading portfolio, where higher priced homes take up a bigger share of the portfolio and the homes that transact more regularly also matter more.

NAR / Census: The data come from surveys of sales of existing single-family houses from NAR affiliates. The national median is calculated by value-weighting the median within each of the nation’s four census regions by the number of single-family homes in each region.

The [Census Bureau index](http://research.stlouisfed.org/fred2/series/mspnhsus) is similar to the NAR index, but it covers new houses as opposed to existing houses. Consequently, the Census index is typically higher than the NAR index, as new houses have historically been higher-priced than existing houses.

FHFA equal-weights house prices and includes refinances, whereas the Case-Shiller and CoreLogic indexes do not.

The Case-Shiller and CoreLogic indexes include all available arm’s-length transactions on single-family homes, including sales financed with nonconforming mortgages, such as jumbo, Alt-A and subprime mortgages. As a result, these indexes include sales of higher-priced homes and transactions with more-volatile sales prices. As indicated earlier, these two indexes value-weight transactions so that higher-priced homes have greater effects on the index.

2. More granular

For a long time, price indexes were just the median price of all homes sold, like the NAR and Census bureau data. Then came Case and Shiller, who made the astute observation that the median price is nonsense, because the homes being sold in one year could be totally different from those sold in another year. They invented the repeat sales index, which makes sure we are tracking the same homes. But, because CS kicks out about 90% of the transactinos, it's left with very high quality, but few transactions. As a result, CS doesnt produce data for every region, just nationally and for the 20 biggest MSAs.

By contrast, Zillow's sample size is much larger, since they're not kicking out any transactions. As a result they can go down much deeper, producing data for every tiny region across the country.

Less than about .5% of the housing stock transacts every month (Fleming, [here](https://wp.zillowstatic.com/3/D_LDTAppreciatingtheDifferences091313-55384b.pdf))
CS: National index is not constant transaction weighted- Based on transaction volumes in each market
excludes homes "which would exclude newly constructed homes that have only had one owner, and/or homes with only one owner that have been occupied for a very long time. Additionally, Case-Shiller’s methodology includes distressed sales, which can distort market swings."

*--> chart: map of zillow price changes y/y, by most granular area (Use TigerLines)*

3. More timely

CS and FHFA are both highly delayed of 2 months from the end of the month the index measures (ie, Jan data released in late March). With CS, its because xyz. With FHFA, its because it takes a while to get the data into the system, because its based on GSE-bought mortgages. By contrast, because Zillow is basing it on transactions in real time, they can deliver the data a month faster than the others (3 weeks from end of measured month). There are so neat applications of this. For example, using Zillow data, you can predict where CS will come out. 











Key points:

-Usefulness is a function of: 
- timeliness and frequency (early and often)
- accuracy (measures what you want it to)
- low revisions
- access (free, ideally in API, etc)
- methodological soundness
- easy to understand

There are three kinds of indexes:

1. Median sales (NAR, Census): Reflects different homes over time. In the case of Census, highly delayed, and infrequent (only quarterly). Some benefits, like can match it to other census data (eg—which demographic is seeing their homes go up the most). NAR's data locked up behind a paywall and very expensive. 
2. Repeat sales (Case-Shiller, FHFA): Reflects the same homes, but may still reflect changing composition of sales. Frequent (monthly, and FHFA releases more granular data quarterly). Easy to access.
3. Autoregressive sales indexes (Zillow): Uses large data sets to tease out the time element of sales prices, controlling for all other factors. Monthly. Very easy to access, including API. More timely than others (released earlier). May pickup changes more slowly.

What do we want from an index?

It should be representative of what we are trying to measure: broadly, American housing. It should be released frequently, and with a small lag from what it's measuring. It should not be subject to frequent revisions. It should be easy to access (free), and easy to understand. It should not be overly volatile. It should reflect changes in market direction early.