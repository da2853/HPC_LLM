import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import os
import pandas as pd
import re
import time
import random
import logging
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from tqdm import tqdm
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import trafilatura
import json
from datetime import datetime

# Add this import at the top of the file if not already present
import re

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WebScraper:
    def __init__(self, base_url, output_folder, url_file='scraped_urls.json'):
        self.base_url = base_url
        self.output_folder = output_folder
        self.visited_urls = set()
        self.url_file = url_file
        self.scraped_urls = self.load_scraped_urls()
        self.use_selenium = False
        self.driver = None

        # Add a user-agent header
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        # Retry logic
        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('http://', HTTPAdapter(max_retries=retries))
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def load_scraped_urls(self):
        try:
            with open(self.url_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_scraped_urls(self):
        with open(self.url_file, 'w') as f:
            json.dump(self.scraped_urls, f, indent=2)

    def get_page_content(self, url):
        # Add a delay before the first request
        time.sleep(random.uniform(2, 5))
        
        try:
            response = self.session.get(url, headers=self.headers)
            response.raise_for_status()
            return response.text
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error fetching {url} with requests: {str(e)}. Trying with Selenium.")
            return self.get_page_content_selenium(url)

    def get_page_content_selenium(self, url):
        if not self.driver:
            chrome_options = Options()
            chrome_options.add_argument("--headless")
            chrome_options.add_argument(f"user-agent={self.headers['User-Agent']}")
            self.driver = webdriver.Chrome(options=chrome_options)

        try:
            self.driver.get(url)
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            return self.driver.page_source
        except Exception as e:
            logger.error(f"Error fetching {url} with Selenium: {str(e)}")
            return None

    def save_page(self, url, content):
        parsed_url = urlparse(url)
        # Sanitize the file path
        safe_path = re.sub(r'[<>:"/\\|?*]', '_', parsed_url.path.strip('/'))
        file_path = os.path.join(self.output_folder, parsed_url.netloc, safe_path)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(f"{file_path}.html", 'w', encoding='utf-8') as f:
            f.write(content)

    def scrape_page(self, url):
        current_time = datetime.now().isoformat()
        
        if url in self.scraped_urls:
            logger.info(f"Already scraped: {url}")
            return []
        
        self.visited_urls.add(url)
        logger.info(f"Scraping: {url}")
        
        content = self.get_page_content(url)
        
        if not content:
            logger.error(f"Failed to fetch content for {url}")
            return []

        self.save_page(url, content)
        
        # Update scraped_urls dictionary
        self.scraped_urls[url] = current_time
        self.save_scraped_urls()
        
        soup = BeautifulSoup(content, 'html.parser')
        links = soup.find_all('a', href=True)
        
        new_urls = []
        for link in links:
            new_url = urljoin(self.base_url, link['href'])
            if new_url.startswith(self.base_url) and new_url not in self.visited_urls:
                new_urls.append(new_url)
        
        # delay to prevent IP blocking
        time.sleep(random.uniform(1, 3))  # Reduced delay
        return new_urls

    def scrape(self):
        urls_to_scrape = [self.base_url]
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            while urls_to_scrape:
                new_urls = list(executor.map(self.scrape_page, urls_to_scrape))
                urls_to_scrape = [url for sublist in new_urls for url in sublist if url not in self.scraped_urls]

        if self.driver:
            self.driver.quit()

def extract_main_content(html_content):
    """
    Extract the main data from the html file.
    """
    extracted = trafilatura.extract(html_content, include_links=False, include_images=False, include_tables=False)
    if extracted:
        cleaned = re.sub(r'\s+', ' ', extracted).strip()  # Remove extra whitespace
        cleaned = re.sub(r'\n+', '\n', cleaned)  # Normalize newlines
        return cleaned
    return None

def clean_data(input_folder, output_file):
    data = []
    
    for root, dirs, files in os.walk(input_folder):
        for file in tqdm(files, desc="Cleaning data"):
            if file.endswith('.html'):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                        # Extract main content
                        main_content = extract_main_content(content)
                        
                        if main_content:
                            data.append({
                                'file': file_path,
                                'content': main_content
                            })
                        else:
                            logger.warning(f"No main content extracted from {file_path}")
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {str(e)}")
    
    # Create a DataFrame and save to CSV
    df = pd.DataFrame(data)
    df.to_csv(output_file, index=False)
    logger.info(f"Cleaned data saved to {output_file}")

def prepare_for_rag(cleaned_data_file, output_file, chunk_size=1000):
    """
    Split the cleaned data into chunks for better RAG performance.
    """
    df = pd.read_csv(cleaned_data_file)
    
    rag_data = []

    for idx, row in df.iterrows():
        content = row['content']
        chunks = []
        current_chunk = ""
        
        sentences = re.split(r'(?<=[.!?])\s+', content)
        
        for sentence in sentences:
            if len(current_chunk) + len(sentence) > chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                current_chunk += " " + sentence
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        for chunk_num, chunk in enumerate(chunks):
            rag_data.append({
                'file': row['file'],
                'chunk_id': chunk_num, 
                'chunk': chunk
            })
    
    rag_df = pd.DataFrame(rag_data)
    rag_df.to_csv(output_file, index=False)
    logger.info(f"RAG-prepared data saved to {output_file}")

if __name__ == "__main__":
    base_url = "https://sites.google.com/nyu.edu/nyu-hpc/"
    output_folder = "scraped_data_nyu_hpc"
    cleaned_output = "cleaned_data_nyu_hpc.csv"
    rag_output = "rag_prepared_data_nyu_hpc.csv"

    # Step 1: Scrape the website
    scraper = WebScraper(base_url, output_folder, url_file='nyu_hpc_scraped_urls.json')
    scraper.scrape()

    # Step 2: Clean the data
    clean_data(output_folder, cleaned_output)

    # Step 3: Prepare for RAG
    prepare_for_rag(cleaned_output, rag_output, chunk_size=1000)

    print("Task Completed")