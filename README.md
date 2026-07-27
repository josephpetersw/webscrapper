# PhonePlaceKenya Scraper

A robust, concurrent, and UI-driven web scraping tool designed specifically for PhonePlaceKenya.com. This project handles Cloudflare bot protection using `curl_cffi`, supports high concurrency via `asyncio`, and presents a beautiful, responsive dashboard using React, TailwindCSS (Vristo theme styling), and Flask.

## Features
- **Smart Bot Evasion**: Bypasses Cloudflare checks seamlessly using `curl_cffi` impersonating a real browser.
- **Concurrent Scraping**: Downloads product pages and images rapidly using asynchronous tasks (configurable workers).
- **Structured Storage**: Organizes scraped data automatically into nested folders (`data/structured/Category/Brand/Product/`).
- **Markdown Descriptions**: Automatically converts HTML product descriptions into clean `.md` files for easy reading and editing.
- **Beautiful UI**: A full-featured React dashboard to monitor scraping progress, view logs, explore local files, and browse products in a Card or List layout.
- **Metrics Dashboard**: Track total scraped products, categories, brands, and images at a glance.
- **Multiple Export Channels**: Export your clean data to JSON, CSV, XML, Excel (.xlsx), or download the entire structured image/markdown repository as a ZIP archive!

## Prerequisites
- Python 3.9+
- Node.js 18+ & npm

## Setup Guide

### 1. Backend Setup
1. Open a terminal (or Command Prompt / PowerShell on Windows) in the project root directory.
2. Create a Python virtual environment:
   ```bash
   python -m venv venv
   ```
3. Activate the virtual environment:
   - **Linux / macOS**:
     ```bash
     source venv/bin/activate
     ```
   - **Windows**:
     ```cmd
     venv\Scripts\activate
     ```
4. Install the required Python packages:
   ```bash
   pip install flask flask-cors beautifulsoup4 lxml curl_cffi aiofiles markdownify pandas openpyxl
   ```

### 2. Frontend Setup
1. Navigate to the `frontend/` directory:
   ```bash
   cd frontend
   ```
2. Install the Node modules:
   ```bash
   npm install
   ```
3. Build the React frontend for production:
   ```bash
   npm run build
   ```

## Running the Application

This project uses a single Flask server to serve both the Backend APIs and the compiled React Frontend. 

1. Ensure your virtual environment is active.
2. Start the Flask server from the root directory:
   - **Linux / macOS**:
     ```bash
     python app.py
     ```
   - **Windows**:
     ```cmd
     python app.py
     ```
3. Open your browser and navigate to:
   **http://127.0.0.1:5000**

## Usage
1. On the dashboard, enter a specific URL (or leave blank to scrape from the sitemap).
2. Set the number of **Concurrent Workers** (e.g., 20 or 50) depending on your internet connection and machine.
3. Click **Start**. The dashboard will display live logs and a progress bar.
4. Once completed, browse the products via the Grid or List view, search, and filter by category.
5. Export your structured data using the sidebar buttons (Excel, CSV, JSON, XML, or ZIP).

## Data Structure
Extracted data is neatly stored in the `data/structured/` directory:
```
data/
└── structured/
    └── Smartphones/
        └── Realme_Phones/
            └── Realme_5/
                ├── data.json              # Product metadata
                ├── description.md         # Long description in Markdown
                ├── short_description.txt  # Plain text short desc
                └── images/                # High-res downloaded images
```
