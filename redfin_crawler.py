from email.mime import base
import re
import json
import random
from turtle import home
import requests
import logging
import time
import pandas as pd
import argparse
from concurrent.futures import ProcessPoolExecutor
from bs4 import BeautifulSoup
import sqlite3
from datetime import date
from redfin_filters import apply_filters


load_dotenv()

LOGGER = None
HEADER = {
    'User-agent': 'Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko)'
                  ' Chrome/49.0.2623.112 Safari/537.36'
}
SQLITE_DB_PATH = os.getenv('SQLITE_DB_PATH')
DIR_PATH = os.path.dirname(os.path.realpath(__file__))
SQLITE_DB_FULL_PATH = f'{DIR_PATH}/{SQLITE_DB_PATH}'


def construct_proxy(ip_addr, port, user=None, password=None):
    if user:
        return {
            'http': f'http://{user}:{password}@{ip_addr}:{port}',
            'https': f'http://{user}:{password}@{ip_addr}:{port}',
        }

    return {
        'http': f'http://{ip_addr}:{port}',
        'https': f'http://{ip_addr}:{port}',
    }


def create_tables_if_not_exist():
    conn = sqlite3.connect(SQLITE_DB_FULL_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS URLS
             (
             URL                    TEXT    NOT NULL,
             NUM_PROPERTIES         INT,
             NUM_PAGES              INT,
             PER_PAGE_PROPERTIES    INT);''')
    conn.execute('''CREATE TABLE IF NOT EXISTS LISTINGS
             (
             URL            TEXT    NOT NULL,
             INFO           TEXT);''')
    conn.execute('''CREATE TABLE IF NOT EXISTS LISTING_SHORT_DETAILS
             (
             URL                TEXT    NOT NULL,
             NUMBER_OF_ROOMS    INT,
             NAME               TEXT,
             COUNTRY            TEXT,
             REGION             TEXT,
             LOCALITY           TEXT,
             STREET             TEXT,
             POSTAL             TEXT,
             TYPE               TEXT,
             PRICE              REAL
             );''')
    conn.execute('''CREATE TABLE IF NOT EXISTS LISTING_FULL_DETAILS
            (
            URL                 TEXT    NOT NULL,
            DATE                TEXT,
            STATUS              TEXT,
            PRICE               INT,
            NUMBER_ROOMS        INT,
            NUMBER_BATHROOMS    REAL,
            SQFT                INT,
            TIME_ON_REDFIN      INT,
            YEAR                INT,
            LOT_SIZE            REAL,
            REDFIN_PRICE        INT,
            SQFT_PRICE          INT,
            MORTGAGE            INT
            );''')

    conn.close()


def get_page_info(url_and_proxy):
    """Return property count, page count and total properties under a given URL."""
    url, proxy = url_and_proxy

    time.sleep(random.random() * 10)
    session = requests.Session()
    total_properties, num_pages, properties_per_page = None, None, None
    try:
        resp = session.get(url, headers=HEADER, proxies=proxy)
        resp.raise_for_status()

        if resp.status_code == 200:
            bf = BeautifulSoup(resp.text, 'lxml')
            page_description_div = bf.find('div', {'class': 'homes summary'})
            if not page_description_div:
                # The page has nothing!
                return(url, 0, 0, 20)
            page_description = page_description_div.get_text()
            if 'of' in page_description:
                property_cnt_pattern = r'([0-9]+) of ([0-9]+) •*'
                property_cnt_one_page_pattern = r'([0-9]+)•*'
                m = re.match(property_cnt_pattern, page_description)
                # n = re.match(property_cnt_one_page_pattern, page_description)
                if m:
                    properties_per_page = int(m.group(1))
                    total_properties = int(m.group(2))
                # elif n:
                #     properties_per_page = int(n.group(1))
                #     total_properties = properties_per_page
                pages = [int(x.get_text())
                         for x in bf.find_all('a', {'class': "goToPage"})]
                num_pages = max(pages)
            else:
                property_cnt_pattern = r'([0-9]+)•*'
                m = re.match(property_cnt_pattern, page_description)
                if m:
                    properties_per_page = int(m.group(1))
                    total_properties = properties_per_page
                num_pages = 1
    except Exception as e:
        LOGGER.exception('Swallowing exception {} on url {}'.format(e, url))
    return (url, total_properties, num_pages, properties_per_page)


def url_partition(base_url, proxies, max_levels=6):
    """Partition the listings for a given url into multiple sub-urls,
    such that each url contains at most 20 properties.
    """
    # urls = [base_url]
    urls = apply_filters(base_url, base_url)
    # first_iter = True
    num_levels = 0
    partitioned_urls = []
    while urls and (num_levels < max_levels):
        scraper_results = []
        partition_inputs = []

        if proxies:
            rand_move = random.randint(0, len(proxies) - 1)

        # print("urls:", urls)
        for i, url in enumerate(urls):
            print("url:", url)
            if proxies:
                proxy = construct_proxy(
                    *proxies[(rand_move + i) % len(proxies)])
                LOGGER.debug(f"scraping url {url} with proxy {proxy}")
                partition_inputs.append((url, proxy))
            else:
                partition_inputs.append((url, ''))

        with ProcessPoolExecutor(max_workers=min(50, len(partition_inputs))) as executor:
            scraper_results = list(executor.map(
                get_page_info, partition_inputs))

        LOGGER.info('Getting {} results'.format(len(scraper_results)))

        with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
            LOGGER.info('stage {} saving to db!'.format(num_levels))
            values = []
            for result in scraper_results:
                to_nulls = [x if x else 'NULL' for x in result]
                values.append("('{}', {}, {}, {})".format(*to_nulls))
            cursor = db.cursor()
            cursor.execute("""
                INSERT INTO URLS (URL, NUM_PROPERTIES, NUM_PAGES, PER_PAGE_PROPERTIES)
                VALUES {};
            """.format(','.join(values)))

        LOGGER.info('Writing to sqlite {} results'.format(
            len(scraper_results)))
        new_urls = []
        for result in scraper_results:
            if (result[1] and result[2] and result[3] and result[1] > result[2] * result[3]) or (num_levels == 0):
                print("result", result, "base", base_url)
                expanded_urls = apply_filters(result[0], base_url)
                if len(expanded_urls) == 1 and expanded_urls[0] == result[0]:
                    LOGGER.info('Cannot further split {}'.format(result[0]))
                else:
                    new_urls.extend(expanded_urls)
            else:
                partitioned_urls.append(result)
        LOGGER.info('stage {}: running for {} urls. We already captured {} urls'.format(
            num_levels, len(new_urls), len(partitioned_urls)))
        urls = new_urls
        num_levels += 1
        time.sleep(random.randint(2, 5))
    return partitioned_urls


def parse_addresses():
    listing_details = {}
    with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM listings")
        rows = cur.fetchall()
        urls = set()

        for url, json_details in rows:
            if url in urls:
                continue
            urls.add(url)
            listings_on_page = (json.loads(json_details))
            for listing in listings_on_page:
                num_rooms, name, country, region, locality, street, postal, house_type, price = \
                    None, None, None, None, None, None, None, None, None
                listing_url = None
                if (not isinstance(listing, list)) and (not isinstance(listing, dict)):
                    continue

                if isinstance(listing, dict):
                    info = listing
                    if ('url' in info) and ('address' in info):
                        listing_url = info.get('url')
                        address_details = info['address']
                        num_rooms = info.get('numberOfRooms')
                        name = info.get('name')
                        country = address_details.get('addressCountry')
                        region = address_details.get('addressRegion')
                        locality = address_details.get('addressLocality')
                        street = address_details.get('streetAddress')
                        postal = address_details.get('postalCode')
                        house_type = info.get('@type')
                        listing_details[listing_url] = (listing_url, num_rooms, name, country,
                                                        region, locality, street, postal, house_type, price)
                    continue

                for info in listing:
                    if ('url' in info) and ('address' in info):
                        listing_url = info.get('url')
                        address_details = info['address']
                        num_rooms = info.get('numberOfRooms')
                        name = info.get('name')
                        country = address_details.get('addressCountry')
                        region = address_details.get('addressRegion')
                        locality = address_details.get('addressLocality')
                        street = address_details.get('streetAddress')
                        postal = address_details.get('postalCode')
                        house_type = info.get('@type')
                    if 'offers' in info:
                        price = info['offers'].get('price')
                if listing_url:
                    listing_details[listing_url] = (listing_url, num_rooms, name, country,
                                                    region, locality, street, postal, house_type, price)

    with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
        cursor = db.cursor()
        try:
            cursor.executemany("""
                INSERT INTO LISTING_SHORT_DETAILS (
                             URL,
                             NUMBER_OF_ROOMS,
                             NAME     ,
                             COUNTRY  ,
                             REGION    ,
                             LOCALITY  ,
                             STREET    ,
                             POSTAL   ,
                             TYPE      ,
                             PRICE)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, listing_details.values())
        except Exception as e:
            LOGGER.info(e)


def scrape_page(url_proxy):
    time.sleep(random.random() * 16)
    details = []
    try:
        url, proxy = url_proxy
        session = requests.Session()
        resp = session.get(url, headers=HEADER, proxies=proxy)
        bf = BeautifulSoup(resp.text, 'lxml')
        details = [json.loads(x.text) for x in bf.find_all(
            'script', type='application/ld+json')]
    except Exception as e:
        LOGGER.exception('failed for url {}, proxy {}'.format(url, proxy))
    return url, json.dumps(details)


def get_paginated_urls(prefix):
    # Return a set of paginated urls with at most 20 properties each.
    paginated_urls = []
    with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
        cursor = db.execute("""
            SELECT URL, NUM_PROPERTIES, NUM_PAGES, PER_PAGE_PROPERTIES
            FROM URLS
        """)
        seen_urls = set()
        for row in cursor:
            url, num_properties, num_pages, per_page_properties = row
            if prefix and (prefix not in url):
                continue
            if url in seen_urls:
                continue
            if num_properties == 0:
                continue
            urls = []
            if not num_pages:
                urls = [url]
            elif (not num_properties) and int(num_pages) == 1 and per_page_properties:
                urls = ['{},sort=lo-price/page-1'.format(url)]
            elif num_properties < num_pages * per_page_properties:
                # Build per page urls.
                urls = [
                    '{},sort=lo-price/page-{}'.format(url, p) for p in range(1, num_pages + 1)]
            paginated_urls.extend(urls)
    return list(set(paginated_urls))


def crawl_redfin_with_proxies(proxies, prefix=''):
    small_urls = get_paginated_urls(prefix)

    if proxies:
        rand_move = random.randint(0, len(proxies) - 1)

    scrape_inputs, scraper_results = [], []
    for i, url in enumerate(small_urls):
        if proxies:
            proxy = construct_proxy(*proxies[(rand_move + i) % len(proxies)])
            scrape_inputs.append((url, proxy))
        else:
            scrape_inputs.append((url, ''))

    with ProcessPoolExecutor(max_workers=min(50, len(scrape_inputs))) as executor:
        scraper_results = list(executor.map(scrape_page, scrape_inputs))

    LOGGER.warning('Finished scraping!')

    with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
        cursor = db.cursor()
        for result in scraper_results:
            url, info = result
            try:
                cursor.execute("""
                    INSERT INTO LISTINGS (URL, INFO)
                    VALUES (?, ?)""", (url, info))
            except Exception as e:
                LOGGER.info('failed record: {}'.format(result))
                LOGGER.info(e)


def get_listing_urls(prefix):
    # Return a set of paginated urls with at most 20 properties each.
    urls = []
    with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
        cursor = db.execute("""
            SELECT URL FROM LISTING_SHORT_DETAILS
        """)
        for row in cursor:
            urls.append(prefix + row[0])

        return urls


def scrape_redfin_listing(url_proxy):
    time.sleep(random.random() * 16)
    price, num_beds, num_baths, sqft = 0, 0, 0, 0
    time_on, year, lot, redfin_price, sqft_price = 0, 0, 0, 0, 0
    mortgage = 0
    try:
        url, proxy = url_proxy
        session = requests.Session()
        resp = session.get(url, headers=HEADER, proxies=proxy)
        bf = BeautifulSoup(resp.text, 'lxml')
        home_main_stats_div = bf.find(
            'div', {'class': 'home-main-stats-variant'})
        if home_main_stats_div:
            price_div = home_main_stats_div.find(
                'div', {'data-rf-test-id': 'abp-price'})
            beds_div = home_main_stats_div.find(
                'div', {'data-rf-test-id': 'abp-beds'})
            baths_div = home_main_stats_div.find(
                'div', {'data-rf-test-id': 'abp-baths'})
            sqft_div = home_main_stats_div.find(
                'div', {'data-rf-test-id': 'abp-sqFt'})
            price_txt = price_div.get_text()
            beds_txt = beds_div.get_text()
            baths_txt = baths_div.get_text()
            sqft_txt = sqft_div.get_text()
            price_pattern = r'\$([0-9,]+)*'
            beds_pattern = r'([0-9]+)Beds*'
            baths_pattern = r'([0-9.]+)Baths*'
            sqft_pattern = r'([0-9,]+)Sq Ft*'
            m_price = re.match(price_pattern, price_txt)
            m_beds = re.match(beds_pattern, beds_txt)
            m_baths = re.match(baths_pattern, baths_txt)
            m_sqft = re.match(sqft_pattern, sqft_txt)
            if m_price:
                price = int(re.sub(r'[,]', '', m_price.group(1)))
            if m_beds:
                num_beds = int(m_beds.group(1))
            if m_baths:
                num_baths = float(m_baths.group(1))
            if m_sqft:
                sqft = int(re.sub(r'[,]', '', m_sqft.group(1)))

        page_content_div = bf.find('div', {'class': 'content clear-fix'})
        if page_content_div:
            status_pattern = r'Status([a-zA-Z]+)*'
            time_on_redfin_pattern = r'Time on Redfin([0-9]+)*'
            year_built_pattern = r'Year Built([0-9]+)*'
            lot_size_pattern = r'Lot Size([0-9,]+)*'
            redfin_estimate_pattern = r'Redfin Estimate\$([0-9,]+)*'
            price_sqrt_pattern = r'Price/Sq.Ft.\$([0-9,]+)*'

            key_details = page_content_div.findChildren(
                'div', {'class': 'keyDetail'})
            for d_div in key_details:
                d_text = d_div.get_text()
                m_status = re.match(status_pattern, d_text)
                m_time_on = re.match(time_on_redfin_pattern, d_text)
                m_year = re.match(year_built_pattern, d_text)
                m_lot = re.match(lot_size_pattern, d_text)
                m_redfin_price = re.match(redfin_estimate_pattern, d_text)
                m_sqft_price = re.match(price_sqrt_pattern, d_text)
                if m_status:
                    status = m_status.group(1)
                if m_time_on:
                    time_on = int(m_time_on.group(1))
                if m_year:
                    year = int(m_year.group(1))
                if m_lot:
                    lot = float(re.sub(r'[,]', '', m_lot.group(1)))
                    # if lot size in acres, convert to sqft
                    if lot < 100:
                        lot *= 43560
                if m_redfin_price:
                    redfin_price = int(
                        re.sub(r'[,]', '', m_redfin_price.group(1)))
                if m_sqft_price:
                    sqft_price = int(re.sub(r'[,]', '', m_sqft_price.group(1)))

        calculator_summary_div = bf.find('div', {'class': 'CalculatorSummary'})

        if calculator_summary_div:
            mortgage_text = calculator_summary_div.get_text()
            mortgage_pattern = r'\$([0-9,]+)* per month'
            m_mortgage = re.match(mortgage_pattern, mortgage_text)
            if m_mortgage:
                mortgage = int(re.sub(r'[,]', '', m_mortgage.group(1)))

    except Exception as e:
        LOGGER.exception('failed for url {}, proxy {}'.format(url, proxy))

    return url, (status, price, num_beds, num_baths,
                 sqft, time_on, year, lot,
                 redfin_price, sqft_price, mortgage)


def crawl_redfin_listings(proxies, prefix="https://redfin.com"):
    # Get listing urls
    # Iterate over urls and scrape listing page
    # Extract bedrooms, bathrooms, sqft, year, price/sqft, price, redfin price,
    # Write data to db
    listing_urls = get_listing_urls(prefix)

    if proxies:
        rand_move = random.randint(0, len(proxies) - 1)

    scrape_inputs, scraper_results = [], []
    for i, url in enumerate(listing_urls):
        if proxies:
            proxy = construct_proxy(*proxies[(rand_move + i) % len(proxies)])
            scrape_inputs.append((url, proxy))
        else:
            scrape_inputs.append((url, ''))

    with ProcessPoolExecutor(max_workers=min(50, len(scrape_inputs))) as executor:
        scraper_results = list(executor.map(
            scrape_redfin_listing, scrape_inputs))

    LOGGER.info('Finished scraping listings!')
    today = date.today().strftime('%Y/%m/%d')

    with sqlite3.connect(SQLITE_DB_FULL_PATH) as db:
        cursor = db.cursor()
        for result in scraper_results:
            url, info = result
            (status, price, num_beds, num_baths,
             sqft, time_on, year, lot,
             redfin_price, sqft_price, mortgage) = info
            print("Listing info:", url, info)
            try:
                cursor.execute("""
                    INSERT INTO LISTING_FULL_DETAILS (
                        URL,
                        DATE,
                        STATUS,
                        PRICE,
                        NUMBER_ROOMS,
                        NUMBER_BATHROOMS,
                        SQFT,
                        TIME_ON_REDFIN,
                        YEAR,
                        LOT_SIZE,
                        REDFIN_PRICE,
                        SQFT_PRICE,
                        MORTGAGE
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                               (url, today, status, price,
                                num_beds, num_baths, sqft, time_on,
                                year, lot, redfin_price, sqft_price, mortgage))
            except Exception as e:
                LOGGER.info('failed record: {}'.format(result))
                LOGGER.info(e)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Scrape Redfin property data.')
    parser.add_argument(
        'redfin_base_url',
        help='Redfin base url to specify the crawling location, '
             'e.g., https://www.redfin.com/city/11203/CA/Los-Angeles/'
    )
    parser.add_argument('--proxy_csv', default='',
                        help='proxies csv path. '
                        'It should contain ip_addr,port,user,password if using proxies with auth. '
                        'Or just contain ip_addr,port columns if no auth needed.')
    parser.add_argument('--type', default='pages',
                        choices=['properties', 'pages',
                                 'property_details', 'filtered_properties'],
                        help='pages or properties (default: properties)')
    parser.add_argument('--property_prefix', default='',
                        help='The property prefix for crawling')
    parser.add_argument('--partition_levels',
                        help="Determine the depth of partition. The higher the more properties scraped.",
                        type=int,
                        default=12)
    parser.add_argument('--logging_level', default='info',
                        choices=['info', 'debug'])
    args = parser.parse_args()

    if args.logging_level == 'info':
        logging.basicConfig(level=logging.INFO)
    elif args.logging_level == 'debug':
        logging.basicConfig(level=logging.DEBUG)
    LOGGER = logging.getLogger(__name__)

    create_tables_if_not_exist()
    redfin_base_url = args.redfin_base_url
    if redfin_base_url[-1] != '/':
        redfin_base_url += '/'

    proxies = None
    if args.proxy_csv:
        proxies = pd.read_csv(args.proxy_csv, encoding='utf-8').values
        print(proxies)

    if args.type == 'pages':
        url_partition(redfin_base_url, proxies,
                      max_levels=args.partition_levels)
    elif args.type == 'properties':
        url_partition(redfin_base_url, proxies,
                      max_levels=args.partition_levels)
        crawl_redfin_with_proxies(proxies)
        parse_addresses()
    elif args.type == 'property_details':
        crawl_redfin_listings(proxies)
        # parse_addresses()
    elif args.type == 'filtered_properties':
        crawl_redfin_with_proxies(proxies, args.property_prefix)
    else:
        raise Exception('Unknown type {}'.format(args.type))
