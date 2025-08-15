---
category: "[[Clippings]]"
author: "[[Skylar Olsen]]"
title: "Zillow Home Value Index Methodology, 2023 Revision: What’s Changed?"
source: https://www.zillow.com/research/methodology-neural-zhvi-32128/
clipped: 2024-03-29
published: 
topics: 
tags: [clippings]
---

[[One Home Price Index to Rule them All?]]

The Zillow Home Value Index (ZHVI) now incorporates the more accurate [neural Zestimate](https://zillow.mediaroom.com/2021-06-15-Zillow-Launches-New-Neural-Zestimate,-Yielding-Major-Accuracy-Gains). 

Starting with Zillow’s January 2023 data release, and for all subsequent releases, the full ZHVI time series has been upgraded to harness the power of the neural Zestimate, which is the Zestimate displayed on home detail pages nationwide. The neural Zestimate employs a neural network that leverages a deeper history of property data — such as sales transactions, tax assessments and public records, in addition to home details such as square footage and location — to react more quickly to current market trends. 

Importantly, the [way the Zillow Home Value Index](https://www.zillow.com/research/zhvi-methodology-2019-deep-26226/) is calculated has not changed, just the Zestimates used to construct it. Because the Zestimates that make up ZHVI are now more accurate, the new ZHVI will better reflect market changes at the national and local levels.

## How ZHVI Works

The Zillow Home Value Index (ZHVI) is designed to capture the value of a typical property across the nation or the neighborhood, not just the homes that sold, and we do so by drawing information from the full distribution of homes in a given region. 

ZHVI measures monthly changes in property-level Zestimates, capturing both the level and appreciation of home values across a wide variety of geographies and housing types (e.g., all single-family homes in ZIP code 98101). This is how we focus on actual market price changes, and not changes in the kinds of markets or property types that sell from month to month. 

**Why Use ZHVI**

The Zillow Home Value Index is optimized to achieve three main objectives:

1.  Timeliness: Data for a given month is published within weeks of the month ending. Other housing indices often publish monthly data at a significant lag of one month or more after the close of a given month. 
2.  Comprehensiveness: ZHVI draws on Zestimates calculated on more than 100 million U.S. homes, including new construction homes and/or homes that have not been listed for sale in many years. This offers a fuller picture than indices that rely solely on data recorded only on those homes that sell in a given period.
3.  Visibility: Because of the way the ZHVI is constructed, it gives users the ability to observe dynamics in very small regions and/or among very specific subsets of homes. The increased responsiveness and accuracy of the neural Zestimate, now behind the ZHVI, should improve small area price signals, making ZHVI even more actionable for users.

Mechanically we do that by taking an aggressively trimmed-mean (middle third) of Zestimates and chaining back with a repeat-Zestimate index. Ever heard of a repeat sales index? Like that, but instead of the same property finally selling again to make a matched pair of home prices the model can use, the ZHVI synthesizes changing neural Zestimates on all individual properties each month, providing insight in neighborhoods and housing segments where other methods fail for lacking enough of the “right kind” of data.

**Alternatives**

Median sale prices capture market forces but are entangled with a changing mix of the kinds of homes that hit the market, sometimes in a seasonal or systematic way, but also in a random, unpredictable way. Summarizing the prices of transacted or listed homes is often important. For example, it will better reflect the total transaction dollars in the industry *that season.*

Some of the most famous home price measures that do control for the changing mix of homes that sell – like the S&P CoreLogic Case-Shiller Home Price Index – are best suited when measuring real estate as a trading portfolio, where higher priced homes take up a bigger share of the portfolio and the homes that transact more regularly also matter more.

## Getting Into the Details: Same Index Method. Big Changes Underneath.

Capturing home price signals from across the market to understand the typical home with the ZHVI starts with the Zestimate. Switching to a neural network-derived Zestimate improved the accuracy of the Zestimate in almost all regions and across all price points. Looking back over the entire year of 2022 – a period of significant market volatility – this new model was nearly 20% more accurate in predicting sales prices than the old model, highlighting the ability of this new state-of-art-machine learning approach to better track volatile markets.

-   Better able to capture turning points, the neural-backed ZHVI was down 4.5% in January 2023 from peak values in July 2022, compared to only 0.5% down over the same period according to the previous version of ZHVI built on an older Zestimate model. The dramatic difference is mostly due to the amplified seasonality of raw neural ZHVI. Smoothed and seasonally adjusted neural ZHVI is only 1.3% down from peak. 
-   Enhanced seasonality also partially explains the pace gap (difference in month-over-month values) during the slowdown between July 2022 and January 2023. However, the acceleration in raw, neural ZHVI month-over-month growth in January was not due to seasonality. Spring price growth doesn’t typically pick up until February or March. 
-   Controlling for seasonality with year-over-year numbers, the two series disagree less: neural ZHVI is up 6.18% year over year and the previous version of ZHVI is up a comparable 6.95%
-   Neural-backed ZHVI increased 44.1% from February 2020 to its pandemic peak in July 2022, versus 41.4% over the same period with the previous version of ZHVI. 

![](https://www.zillowstatic.com/bedrock/app/uploads/sites/37/2023/02/neural-vs-old.png)  
**Neural Zestimates – Why are they different?**

To create the [Neural Zestimate](https://zillow.mediaroom.com/2021-06-15-Zillow-Launches-New-Neural-Zestimate,-Yielding-Major-Accuracy-Gains), we train a neural network model with long and detailed histories of transactions, listings, and property information.  All of this rich, historical data is sourced from our Zillow Database of All Homes – a future-enabling collaboration with county public records offices, MLSs, brokerages, real-estate agents, and individual households across the country. The same information you can comb through on our home pages is put to work for consumers and analysts seeking to understand the price of homes and increasingly volatile housing markets.

The core tech for the previous Zestimate algorithm used random forests, an algorithmic approach that filtered homes into ever narrowing price buckets using property and listing information. The end result is a set of similar homes with prices  that jointly minimizes the error between the model’s predictions (the Zestimates) and actual, observed sale prices.

In this way, the old algorithms drew information from singular home sales and fairly flexibly related those prices to observed home and listing inflation.  Departing from this, the new neural network-based approach has several major advantages. First and foremost, it can handle A LOT more data allowing the models to learn about the price of homes across time and beyond county borders flexibly and responsively.

Once constrained to run on a data sample within a given county, the new neural net learns about natural and man-made geographic boundaries on their own. Training with long histories, the models learn about prices over time and home price seasonality. Where random forests struggle with time series data and can only classify homes into price buckets they’ve actually observed in the data, neural net tech can extrapolate, say, Seattle’s most expensive house from sale prices that haven’t come close yet.

Neural net technologies reach their real accuracy potential with massive, increasingly complex datasets, perfect for our ever-growing Database of All Homes. Switching to neural Zestimates to underlie the Zillow Home Value Index, for example, removed a persistent upward bias during the late-pandemic housing turning point and surfaced a much more volatile seasonal component to home values and market pricing. 

**ZHVI Forecasting Improvement**

Systematic error, or consistently over or underpredicting an event or the future, is the enemy of clear-eyed decision-making. Reducing systematic error in housing price forecasts during this environment centers around more flexibility and accurately capturing turning points versus seasonal trends. 

Because of the significant improvement in underlying Zestimates, the new ZHVI more accurately reflects market signals across the stock of all homes over time and across space, making home price forecasts more reliable at the national and local levels. Over the test period from January 2020 to September 2022 —  a radically challenging period for forecasting *anything* — the one-month ahead systematic error dropped to practically zero when run on neural Zestimate-built ZHVI. Out-of-sample forecast accuracy (mean absolute error) and systematic error or bias (mean error) improved for our longer-term home price model (3 to 12 month out) as well.

The use of the more accurate and adaptive neural Zestimates in the Zillow Home Value Index marks a radical advancement in the responsiveness and accuracy in this powerful tool in increasingly volatile housing markets. 

**Greater seasonality in raw ZHVI**

That distinction between raw and seasonally adjusted is all about focusing on tactics during the search process (seasonal variation) or expectations over the long run about where values are going (trend). The new, more responsive neural ZHVI is much better at picking up this seasonal component.

Neural ZHVI shows that late fall and winter months cool housing competition and home value growth significantly, giving buyers more bargaining power over available homes. In contrast, in the spring shopping season, home values grow much more aggressively *on the same homes* (this is the power of using Zestimates across the entire market). Month-over-month appreciation typically hits its seasonally slowest pace in December and its fastest pace in May. 

![](https://www.zillowstatic.com/bedrock/app/uploads/sites/37/2023/02/neuralVold-MOM.png)

**Pre-2012 ZHVI history**

Unfortunately, because we need *decades’ worth* of detailed history for the neural network model to learn about home prices AND time, we do not produce historical Zestimates with our neural network prior to 2012. To still provide numerous opportunities for rapid response as well as long run housing research, we stitch the now default and more technologically advanced and responsive ZHVI to the historical index using a technique to harmonize the different seasonality patterns.

## Future work

 The Zillow Economic Research team and other teams at Zillow are committed to continuously improving the accuracy and usability of Zillow’s public housing data. The improved accuracy and sheer scope of the neural Zestimate, along with a treasure trove of other Zillow housing metrics, provide nearly unlimited possibilities to “turn on the lights” for housing researchers, home buyers, sellers, renters and anybody else making a critical housing decision. 

The power is in the Living Database of All Homes and the beautiful brains in Zillow Analytics. #DataWannaBeFree