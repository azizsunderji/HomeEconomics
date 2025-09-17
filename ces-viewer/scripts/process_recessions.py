#!/usr/bin/env python3
"""
Process NBER recession dates from FRED into periods
"""

import pandas as pd
import json
from datetime import datetime

# Read recession data
df = pd.read_csv('../data/USRECDP.csv')
df['observation_date'] = pd.to_datetime(df['observation_date'])
df = df[df['observation_date'] >= '1939-01-01']  # Filter to match our CES data

# Find recession periods
recessions = []
in_recession = False
start_date = None

for _, row in df.iterrows():
    if row['USRECDP'] == 1 and not in_recession:
        # Recession starts
        start_date = row['observation_date']
        in_recession = True
    elif row['USRECDP'] == 0 and in_recession:
        # Recession ends
        end_date = df[df['observation_date'] < row['observation_date']]['observation_date'].iloc[-1]
        recessions.append({
            'start': start_date.strftime('%Y-%m-%d'),
            'end': end_date.strftime('%Y-%m-%d'),
            'start_year': start_date.year,
            'end_year': end_date.year,
            'name': f"{start_date.strftime('%b %Y')} - {end_date.strftime('%b %Y')}"
        })
        in_recession = False

# Add descriptive names for well-known recessions
recession_names = {
    '1945': 'Post-WWII',
    '1948': 'Post-War Adjustment',
    '1953': 'Post-Korean War',
    '1957': 'Eisenhower Recession',
    '1960': 'Rolling Adjustment',
    '1969': 'Nixon Recession',
    '1973': 'Oil Crisis',
    '1980': 'Volcker Recession I',
    '1981': 'Volcker Recession II',
    '1990': 'Gulf War Recession',
    '2001': 'Dot-Com Bust',
    '2007': 'Great Recession',
    '2020': 'COVID-19'
}

for rec in recessions:
    year_str = str(rec['start_year'])
    if year_str in recession_names:
        rec['label'] = f"{recession_names[year_str]} ({rec['name']})"
    else:
        rec['label'] = rec['name']

# Save as JSON
output = {
    'recessions': recessions,
    'updated': datetime.now().isoformat(),
    'source': 'FRED USRECDP (NBER Recession Indicators)'
}

with open('../data/recession_periods.json', 'w') as f:
    json.dump(output, f, indent=2)

print(f"Processed {len(recessions)} recession periods since 1939")
for rec in recessions:
    print(f"  {rec['label']}")