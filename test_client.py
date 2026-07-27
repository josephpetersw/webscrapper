from scraper.client import ScraperClient

if __name__ == '__main__':
    client = ScraperClient()
    print("Fetching sitemap index...")
    soup = client.fetch_soup('https://www.phoneplacekenya.com/sitemap_index.xml')
    if soup:
        sitemaps = [loc.text for loc in soup.find_all('loc')]
        for s in sitemaps:
            print(s)
    else:
        print("Failed to fetch.")
