"""
IPUMS USA Microdata Extract -- Submit, Monitor, Download
ACS 1-year 2005-2023: household structure analysis for 30-year-olds
"""

import requests
import time
import os

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY = "59cba10d8a5da536fc06b59d2762e4c5859b48dbb5215e13b449ba09"
BASE_URL = "https://api.ipums.org"
COLLECTION = "usa"
API_VERSION = 2

HEADERS = {
    "Authorization": API_KEY,
    "Content-Type": "application/json",
}

DATA_DIR = "/Users/azizsunderji/Dropbox/Home Economics/2026_02_19_HomeAt30/data"

# ── Extract Definition ────────────────────────────────────────────────────────
EXTRACT_DEFINITION = {
    "description": "ACS 1-year 2005-2023: household structure for 30-year-olds analysis",
    "dataStructure": {
        "rectangular": {
            "on": "P"
        }
    },
    "dataFormat": "csv",
    "samples": {
        "us2005a": {}, "us2006a": {}, "us2007a": {}, "us2008a": {}, "us2009a": {},
        "us2010a": {}, "us2011a": {}, "us2012a": {}, "us2013a": {}, "us2014a": {},
        "us2015a": {}, "us2016a": {}, "us2017a": {}, "us2018a": {}, "us2019a": {},
        "us2021a": {}, "us2022a": {}, "us2023a": {},
    },
    "variables": {
        "YEAR": {}, "STATEFIP": {}, "PUMA": {}, "MET2013": {},
        "PERWT": {}, "HHWT": {},
        "AGE": {}, "SEX": {}, "RACE": {}, "HISPAN": {}, "MARST": {},
        "RELATE": {},
        "EDUC": {}, "EDUCD": {}, "DEGFIELD": {},
        "EMPSTAT": {}, "EMPSTATD": {}, "INCTOT": {}, "INCWAGE": {}, "FTOTINC": {},
        "HHINCOME": {},
        "RENT": {}, "OWNERSHP": {},
        "GQ": {},
        "NCHILD": {}, "BIRTHYR": {},
        "PERNUM": {}, "SERIAL": {},
    },
}


def submit_extract():
    url = f"{BASE_URL}/extracts?collection={COLLECTION}&version={API_VERSION}"
    response = requests.post(url, headers=HEADERS, json=EXTRACT_DEFINITION)
    if response.status_code == 200:
        data = response.json()
        extract_number = data["number"]
        print(f"Extract submitted. Number: {extract_number}, Status: {data['status']}")
        return extract_number
    else:
        print(f"ERROR {response.status_code}: {response.text}")
        return None


def check_status(extract_number):
    url = f"{BASE_URL}/extracts/{extract_number}?collection={COLLECTION}&version={API_VERSION}"
    response = requests.get(url, headers={"Authorization": API_KEY})
    if response.status_code == 200:
        return response.json()
    else:
        print(f"ERROR {response.status_code}: {response.text}")
        return None


def wait_for_extract(extract_number, poll_interval=30, max_wait=7200):
    elapsed = 0
    while elapsed < max_wait:
        data = check_status(extract_number)
        if data is None:
            return None
        status = data["status"]
        print(f"[{elapsed}s] Status: {status}")
        if status == "completed":
            download_url = data["downloadLinks"]["data"]["url"]
            print(f"Extract ready! URL: {download_url}")
            return download_url
        elif status in ("failed", "canceled"):
            print(f"Extract {status}.")
            return None
        time.sleep(poll_interval)
        elapsed += poll_interval
    print(f"Timed out after {max_wait}s")
    return None


def download_extract(download_url, output_dir=DATA_DIR):
    os.makedirs(output_dir, exist_ok=True)
    filename = download_url.split("/")[-1]
    output_path = os.path.join(output_dir, filename)
    print(f"Downloading to {output_path} ...")
    response = requests.get(download_url, headers={"Authorization": API_KEY}, stream=True)
    total = 0
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)
            print(f"  {total / (1024*1024):.1f} MB", end="\r")
    print(f"\nSaved: {output_path} ({total / (1024*1024):.1f} MB)")
    return output_path


if __name__ == "__main__":
    extract_number = submit_extract()
    if extract_number is None:
        raise SystemExit("Failed to submit extract.")
    download_url = wait_for_extract(extract_number)
    if download_url is None:
        raise SystemExit("Extract did not complete.")
    filepath = download_extract(download_url)
    print(f"\nDone. File at: {filepath}")
