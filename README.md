# XphoneKenyaScraper 🚀

![Status](https://img.shields.io/badge/Status-Active-success)
![Python Version](https://img.shields.io/badge/Python-3.9%2B-blue)
![React Version](https://img.shields.io/badge/React-18.x-61dafb)

A robust, concurrent, and UI-driven web scraping tool designed specifically for XphoneKenya.com. This project handles Cloudflare bot protection, supports high concurrency, and presents a beautiful, responsive dashboard to manage, monitor, and export your scraped data.

---
![alt text](image.png)
## 🛠️ Tech Stack

### Frontend
- **React (Vite)**: Lightning-fast development environment and optimized production builds.
- **Tailwind CSS**: Utility-first CSS framework for rapid UI styling, customized with a premium Vristo-inspired theme (dark/light mode support).
- **Lucide React**: Beautiful, consistent iconography.
- **Fetch API**: Native browser API for seamless communication with the backend.

### Backend
- **Python 3.9+**: The core language powering the scraping engine.
- **Flask**: A lightweight WSGI web application framework serving the API and compiled frontend.
- **asyncio**: Powers the asynchronous, non-blocking scraping architecture to process hundreds of pages concurrently.
- **curl_cffi**: Advanced HTTP client that impersonates real browser TLS fingerprints to seamlessly bypass Cloudflare anti-bot protections.
- **BeautifulSoup4 & lxml**: High-performance HTML parsing and data extraction.
- **markdownify**: Automatically converts messy HTML product descriptions into clean, readable Markdown files.
- **Pandas**: Utilized for robust and flexible Excel (`.xlsx`) data exports.

---

## ✨ Key Features

- **Smart Bot Evasion**: Bypasses Cloudflare checks seamlessly using `curl_cffi` by mimicking legitimate browser TLS fingerprints.
- **High-Speed Concurrent Scraping**: Downloads product pages and images rapidly using asynchronous tasks (with configurable worker limits).
- **Structured File Storage**: Organizes scraped data automatically into a highly structured local filesystem architecture (`data/structured/Category/Brand/Product/`).
- **Markdown Conversion**: Automatically sanitizes and converts HTML product descriptions into clean `.md` files for easy reading and editing.
- **Beautiful Dashboard UI**: A full-featured React dashboard to monitor live scraping progress, view logs, explore local files, and browse products in a grid or list layout.
- **Export Flexibility**: Export your data exactly how you need it. Choose between raw HTML or clean text formats for JSON, CSV, XML, Excel (.xlsx).
- **Archive Generation**: Download the entire structured image and markdown repository as a ZIP archive directly from the UI!

---

## 💻 Local Setup & Installation

Follow these steps to get the project running on your local machine.

### Prerequisites
- [Python 3.9+](https://www.python.org/downloads/)
- [Node.js 18+](https://nodejs.org/) & npm

### 1. Backend Setup

1. **Clone the repository** and navigate to the root directory:
   ```bash
   cd XphoneKenyaScraper
   ```
2. **Create a Python virtual environment**:
   ```bash
   python -m venv venv
   ```
3. **Activate the virtual environment**:
   - **Linux / macOS**:
     ```bash
     source venv/bin/activate
     ```
   - **Windows**:
     ```cmd
     venv\Scripts\activate
     ```
4. **Install the required Python dependencies**:
   ```bash
   pip install flask flask-cors beautifulsoup4 lxml curl_cffi aiofiles markdownify pandas openpyxl gunicorn
   ```

### 2. Frontend Setup

1. **Navigate to the frontend directory**:
   ```bash
   cd frontend
   ```
2. **Install Node modules**:
   ```bash
   npm install
   ```
3. **Build the React frontend** (This bundles the app into `frontend/dist` which Flask serves):
   ```bash
   npm run build
   ```
   *(Note: If you plan on actively developing the UI, you can run `npm run dev` in this directory to start the Vite hot-reload server at `http://localhost:5173`)*

---

## 🚀 Running the Application

This project uses a unified Flask server to serve both the Backend REST APIs and the compiled React Frontend UI.

### 1. Activate the Virtual Environment
Before starting the server, you must activate the Python virtual environment in your terminal from the project root directory.

- **On Linux / macOS:**
  ```bash
  source venv/bin/activate
  ```
- **On Windows (Command Prompt / PowerShell):**
  ```cmd
  venv\Scripts\activate
  ```

### 2. Start the Server

**Option A: Production Server (Recommended - High Speed)**
This runs the app with 4 concurrent worker processes, ensuring blazing fast UI performance and instantaneous data exports without freezing.
```bash
./venv/bin/gunicorn --workers 4 --bind 0.0.0.0:5000 app:app
```
*(Note: Gunicorn is natively supported on Linux/macOS. If you are on Windows, use Option B instead).*

**Option B: Development Server (Basic/Standard)**
If you do not have Gunicorn installed or are running natively on Windows, you can start the standard Python development server:
```bash
./venv/bin/python app.py
```

### 3. Access the Dashboard
Once the server is running (using either option), open your web browser and navigate to:
**[http://127.0.0.1:5000](http://127.0.0.1:5000)**

---

## 📖 How to Use

1. **Configure Scraping**: On the dashboard, enter a specific product URL to scrape, or leave it blank to scrape the entire sitemap.
2. **Set Workers**: Adjust the number of **Concurrent Workers** (e.g., 20 or 50). Higher numbers scrape faster but require a stable internet connection.
3. **Start**: Click **Start**. The dashboard will transition to the Live Logs view, displaying real-time scraping progress and terminal output.
4. **Browse**: Once completed, browse the extracted products via the Grid or List view, search by keywords, and filter by category.
5. **Export**: Use the sidebar buttons to export your structured data. **Tip:** Use the "Clean HTML from exports" toggle to choose whether you want raw HTML tags or clean plain text in your CSV/Excel files!

---

## 📁 Data Structure

Extracted data is neatly and permanently stored in the `data/structured/` directory:

```text
data/
└── structured/
    └── Smartphones/
        └── Realme_Phones/
            └── Realme_5/
                ├── data.json              # Product metadata (price, URL, etc.)
                ├── description.md         # Full long description converted to Markdown
                ├── short_description.txt  # Clean plain-text short description
                └── images/                # High-res downloaded product images (.jpg/.png)
```
