import { useState, useEffect, useRef } from 'react';
import { Package, Folder, Terminal, Download, StopCircle, PlayCircle, Menu, Moon, Sun, Monitor, Loader2, Image as ImageIcon, CheckCircle, ChevronRight, X, Search, ChevronLeft, Filter, LayoutGrid, List, RefreshCw, Users, Activity, Server, Database, HardDrive, Globe, Cpu, FileText, Layers, AlertTriangle, XCircle } from 'lucide-react';
import './index.css';

const API = 'http://localhost:5000';

export default function App() {
  const [url, setUrl] = useState('');
  const [workers, setWorkers] = useState(20);
  const [logs, setLogs] = useState([]);
  const [progress, setProgress] = useState({ current: 0, total: 0, eta: 0 });
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState(null);
  
  const [view, setView] = useState('products');
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(20);
  const [totalPages, setTotalPages] = useState(0);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [allCategories, setAllCategories] = useState([]);
  const [listMode, setListMode] = useState(false);
  const [stats, setStats] = useState({ total_products: 0, total_categories: 0, total_brands: 0, total_images: 0 });
  const [systemStatus, setSystemStatus] = useState({ overall: 'operational', checked_at: null, services: [] });
  
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState([]);
  const [loading, setLoading] = useState(false);
  const [scraping, setScraping] = useState(false);
  const [exporting, setExporting] = useState(null);
  const [isDark, setIsDark] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('theme') === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches);
    }
    return true;
  });
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const logsEndRef = useRef(null);
  const stateRef = useRef({ page: 1, search: '', category: '' });

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark');
      localStorage.setItem('theme', 'dark');
    } else {
      document.documentElement.classList.remove('dark');
      localStorage.setItem('theme', 'light');
    }
  }, [isDark]);
  
  useEffect(() => {
    stateRef.current = { page, search, category };
    fetchProducts();
  }, [page, limit, search, category]);

  useEffect(() => {
    fetchFiles();
    fetchStatus();
    fetchSystemStatus();
    const interval = setInterval(() => {
      fetchLogs();
      fetchProgress();
      fetchStatus();
      fetchSystemStatus();
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (view === 'logs') logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs, view]);

  const fetchLogs = async () => {
    try {
      const res = await fetch(`${API}/api/logs?lines=100`);
      const data = await res.json();
      const lines = (data.logs || '').split('\n').filter(Boolean);
      setLogs(lines);
    } catch {}
  };

  const fetchProgress = async () => {
    try {
      const res = await fetch(`${API}/api/progress`);
      const data = await res.json();
      setProgress(data);
    } catch {}
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API}/api/status`);
      const data = await res.json();
      setScraping(data.running);
    } catch {}
  };

  const fetchStats = async () => {
    try {
      const res = await fetch(`${API}/api/stats`);
      const data = await res.json();
      setStats(data);
    } catch {}
  };

  const fetchSystemStatus = async () => {
    try {
      const res = await fetch(`${API}/api/system/status`);
      const data = await res.json();
      setSystemStatus(data);
    } catch {}
  };

  const handleExport = async (type, endpoint) => {
    setExporting(type);
    try {
      const res = await fetch(`${API}${endpoint}`);
      const blob = await res.blob();
      
      const disposition = res.headers.get('Content-Disposition');
      let filename = `export_${type}`;
      if (disposition && disposition.indexOf('filename=') !== -1) {
        filename = disposition.split('filename=')[1].replace(/["']/g, '');
      } else {
        if (endpoint.endsWith('json')) filename += '.json';
        else if (endpoint.endsWith('csv')) filename += '.csv';
        else if (endpoint.endsWith('excel')) filename += '.xlsx';
        else if (endpoint.endsWith('xml')) filename += '.xml';
        else filename += '.zip';
      }

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (e) {
      console.error('Export failed', e);
    }
    setExporting(null);
  };

  const fetchProducts = async () => {
    try {
      const res = await fetch(`${API}/api/products?page=${page}&limit=${limit}&search=${encodeURIComponent(search)}&category=${encodeURIComponent(category)}`);
      const data = await res.json();
      setProducts(data.data || []);
      setTotalPages(data.totalPages || 0);
      if (data.categories) setAllCategories(data.categories);
    } catch (e) {
      console.error('Failed to fetch products', e);
    }
  };

  useEffect(() => {
    if (view === 'products') {
      fetchProducts();
      fetchStats();
    }
  }, [view, page, limit, search, category, progress.total]);

  const fetchFiles = async (path = '') => {
    try {
      const res = await fetch(`${API}/api/files${path ? `?path=${encodeURIComponent(path)}` : ''}`);
      const data = await res.json();
      setFiles(data);
    } catch {}
  };

  const startScrape = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url || null, workers: workers })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setUrl('');
        setScraping(true);
      }
    } catch {}
    setLoading(false);
  };

  const stopScrape = async () => {
    try {
      await fetch(`${API}/api/scrape/stop`, { method: 'POST' });
      setScraping(false);
    } catch {}
  };

  const navigateTo = (node) => {
    if (node.type === 'directory') {
      const newPath = [...currentPath, node];
      setCurrentPath(newPath);
      fetchFiles(node.path);
    } else {
      window.open(`${API}/data/${node.path}`, '_blank');
    }
  };

  const navigateUp = () => {
    const newPath = currentPath.slice(0, -1);
    setCurrentPath(newPath);
    fetchFiles(newPath.length > 0 ? newPath[newPath.length - 1].path : '');
  };

  const percentage = progress.total > 0 ? Math.round((progress.current / progress.total) * 100) : 0;
  const hasProducts = products.length > 0 || search || category;

  const formatETA = (seconds) => {
    if (!seconds || seconds <= 0) return '';
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    if (m > 0) return `ETA: ${m}m ${s}s`;
    return `ETA: ${s}s`;
  };

  const getPaginationGroup = () => {
    let start = Math.max(1, page - 2);
    let end = Math.min(totalPages, start + 4);
    if (end - start < 4) {
      start = Math.max(1, end - 4);
    }
    return Array.from({ length: Math.max(0, end - start + 1) }, (_, idx) => start + idx);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-[#fafafa] dark:bg-[#060818] font-sans text-sm text-[#888ea8]">
      
      <aside className={`flex-none bg-white dark:bg-[#0e1726] border-r border-white-light dark:border-[#1b2e4b] transition-all duration-300 ${sidebarOpen ? 'w-[260px]' : 'w-0 overflow-hidden'}`}>
        <div className="flex items-center px-6 py-5 border-b border-white-light dark:border-[#1b2e4b]">
          <Monitor className="w-6 h-6 text-primary mr-2" />
          <h2 className="text-xl font-bold text-black dark:text-white tracking-tight">Scraper<span className="text-primary font-normal">Pro</span></h2>
        </div>

        <div className="p-4 space-y-1">
          <button onClick={() => setView('products')} className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-md transition-colors ${view === 'products' ? 'bg-primary text-white shadow-[0_10px_20px_-10px_rgba(67,97,238,0.44)]' : 'hover:bg-white-light/30 dark:hover:bg-[#1b2e4b] text-black dark:text-[#506690]'}`}>
            <Package className="w-5 h-5" />
            <span className="font-semibold">Products View</span>
          </button>
          
          <button onClick={() => { setView('files'); fetchFiles(); setCurrentPath([]); }} className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-md transition-colors ${view === 'files' ? 'bg-primary text-white shadow-[0_10px_20px_-10px_rgba(67,97,238,0.44)]' : 'hover:bg-white-light/30 dark:hover:bg-[#1b2e4b] text-black dark:text-[#506690]'}`}>
            <Folder className="w-5 h-5" />
            <span className="font-semibold">File Explorer</span>
          </button>

          <button onClick={() => setView('logs')} className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-md transition-colors ${view === 'logs' ? 'bg-primary text-white shadow-[0_10px_20px_-10px_rgba(67,97,238,0.44)]' : 'hover:bg-white-light/30 dark:hover:bg-[#1b2e4b] text-black dark:text-[#506690]'}`}>
            <Terminal className="w-5 h-5" />
            <span className="font-semibold">Live Logs</span>
            {scraping && <span className="ml-auto w-2 h-2 rounded-full bg-success animate-ping"></span>}
          </button>

          <button onClick={() => setView('status')} className={`w-full flex items-center gap-3 px-4 py-2.5 rounded-md transition-colors ${view === 'status' ? 'bg-primary text-white shadow-[0_10px_20px_-10px_rgba(67,97,238,0.44)]' : 'hover:bg-white-light/30 dark:hover:bg-[#1b2e4b] text-black dark:text-[#506690]'}`}>
            <Activity className="w-5 h-5" />
            <span className="font-semibold">System Status</span>
            <span className={`ml-auto w-2 h-2 rounded-full ${
              systemStatus.overall === 'operational' ? 'bg-success' :
              systemStatus.overall === 'degraded' ? 'bg-warning animate-pulse' :
              systemStatus.overall === 'down' ? 'bg-danger animate-ping' : 'bg-[#888ea8]'
            }`}></span>
          </button>
        </div>

        <div className="mt-auto p-4 border-t border-white-light dark:border-[#1b2e4b]">
            <div className="flex flex-col gap-2 mb-4">
              <button onClick={() => handleExport('json', '/api/export/json')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'json' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export JSON
              </button>
              
              <button onClick={() => handleExport('csv_clean', '/api/export/csv?clean=true')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'csv_clean' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export CSV (Clean)
              </button>
              <button onClick={() => handleExport('csv_raw', '/api/export/csv?clean=false')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'csv_raw' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export CSV (Raw HTML)
              </button>
              
              <button onClick={() => handleExport('excel_clean', '/api/export/excel?clean=true')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'excel_clean' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Excel (Clean)
              </button>
              <button onClick={() => handleExport('excel_raw', '/api/export/excel?clean=false')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'excel_raw' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Excel (Raw HTML)
              </button>

              <button onClick={() => handleExport('xml', '/api/export/xml')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'xml' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export XML
              </button>
           </div>
           
           <div className="flex flex-col gap-2 mb-4">
              <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">Data Lists</p>
              <button onClick={() => handleExport('categories_csv', '/api/export/categories_csv')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'categories_csv' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Categories
              </button>
              <button onClick={() => handleExport('brands_csv', '/api/export/brands_csv')} disabled={exporting !== null} className="btn btn-outline-primary w-full gap-2 text-left justify-center">
                {exporting === 'brands_csv' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Brands
              </button>
           </div>
           
           <div className="flex flex-col gap-2">
              <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">ZIP Archives</p>
              <button onClick={() => handleExport('zip_all', '/api/export/structured')} disabled={exporting !== null} className="btn btn-outline-secondary w-full gap-2 text-left justify-center">
                {exporting === 'zip_all' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Everything
              </button>
              <button onClick={() => handleExport('zip_data', '/api/export/structured/data')} disabled={exporting !== null} className="btn btn-outline-secondary w-full gap-2 text-left justify-center">
                {exporting === 'zip_data' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Products Only
              </button>
              <button onClick={() => handleExport('zip_images', '/api/export/structured/images')} disabled={exporting !== null} className="btn btn-outline-secondary w-full gap-2 text-left justify-center">
                {exporting === 'zip_images' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Export Images Only
              </button>
           </div>
        </div>
      </aside>

      <div className="flex-1 flex flex-col overflow-hidden">
        
        <header className="flex-none bg-white dark:bg-[#0e1726] border-b border-white-light dark:border-[#1b2e4b] px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-2 rounded-full hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] transition-colors text-black dark:text-white">
              <Menu className="w-5 h-5" />
            </button>
            <h1 className="text-lg font-semibold text-black dark:text-white hidden xl:block">
              {view === 'products' && 'Product Database'}
              {view === 'files' && 'Data Directory'}
              {view === 'logs' && 'Terminal Output'}
              {view === 'status' && 'System Status'}
            </h1>
          </div>

          <div className="flex items-center gap-4 flex-1 justify-end ml-4">
             <form onSubmit={startScrape} className="flex items-center gap-2 flex-1 max-w-4xl justify-end">
                <input
                  type="text"
                  placeholder="Target URL (leave blank for all)"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={scraping}
                  className="form-input w-full min-w-[200px]"
                />
                
                <div className="relative flex items-center bg-white dark:bg-[#121e32] border border-white-light dark:border-[#17263c] rounded-md px-3 h-[38px] flex-none">
                  <label className="text-[#888ea8] font-semibold text-xs uppercase tracking-wider mr-2 flex items-center gap-1">
                    <Users className="w-3.5 h-3.5" /> Workers
                  </label>
                  <input
                    type="number"
                    min="1" max="100"
                    value={workers}
                    onChange={(e) => setWorkers(parseInt(e.target.value) || 1)}
                    disabled={scraping}
                    className="w-12 bg-transparent text-black dark:text-white text-center font-bold outline-none"
                  />
                </div>

                {scraping ? (
                   <button type="button" onClick={stopScrape} className="btn btn-danger gap-2">
                     <StopCircle className="w-4 h-4" /> Stop
                   </button>
                ) : (
                   <button type="submit" disabled={loading} className="btn btn-primary gap-2">
                     {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />} Start
                   </button>
                )}
             </form>
             
             <div className="h-6 w-px bg-white-light dark:bg-[#1b2e4b]"></div>

             <button onClick={() => setIsDark(!isDark)} className="p-2 rounded-full bg-[#f4f4f4] dark:bg-[#1b2e4b] text-primary hover:bg-primary hover:text-white transition-colors">
               {isDark ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
             </button>
          </div>
        </header>

        {progress.total > 0 && (
          <div className="flex-none sticky top-0 z-50 bg-white/95 dark:bg-[#0e1726]/95 backdrop-blur-sm border-b border-white-light dark:border-[#1b2e4b] px-6 py-3 shadow-sm">
            <div className="flex justify-between items-end mb-2">
              <div className="flex items-center gap-2">
                {scraping ? (
                  <><Loader2 className="w-4 h-4 text-primary animate-spin" /> <span className="font-semibold text-black dark:text-white">Scraping in progress...</span></>
                ) : (
                  <><CheckCircle className="w-4 h-4 text-success" /> <span className="font-semibold text-black dark:text-white">Scraping complete</span></>
                )}
              </div>
              <div className="text-right">
                <span className="font-bold text-primary mr-2">{percentage}%</span>
                <span className="text-xs font-normal text-[#888ea8]">({progress.current.toLocaleString()} / {progress.total.toLocaleString()})</span>
                {scraping && progress.eta > 0 && (
                  <span className="ml-3 text-xs font-semibold text-warning bg-warning/10 px-2 py-1 rounded">
                    {formatETA(progress.eta)}
                  </span>
                )}
              </div>
            </div>
            <div className="w-full h-2 bg-[#ebebeb] dark:bg-[#1b2e4b] rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all duration-500 ${scraping ? 'bg-primary' : 'bg-success'}`} style={{ width: `${percentage}%` }}></div>
            </div>
          </div>
        )}

        <div className="flex-1 overflow-hidden flex flex-col">
          
          {view === 'products' && (
            <div className="flex-1 flex flex-col h-full overflow-hidden">
               {hasProducts && (
                 <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 pb-0 bg-[#fafafa] dark:bg-[#060818]">
                   <div className="panel p-4 flex items-center justify-between border-l-4 border-l-primary">
                     <div>
                       <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">Total Products</p>
                       <h3 className="text-2xl font-bold text-black dark:text-white">{stats.total_products.toLocaleString()}</h3>
                     </div>
                     <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                       <Package className="w-5 h-5" />
                     </div>
                   </div>
                   <div className="panel p-4 flex items-center justify-between border-l-4 border-l-secondary">
                     <div>
                       <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">Categories</p>
                       <h3 className="text-2xl font-bold text-black dark:text-white">{stats.total_categories.toLocaleString()}</h3>
                     </div>
                     <div className="w-10 h-10 rounded-full bg-secondary/10 flex items-center justify-center text-secondary">
                       <Folder className="w-5 h-5" />
                     </div>
                   </div>
                   <div className="panel p-4 flex items-center justify-between border-l-4 border-l-success">
                     <div>
                       <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">Brands</p>
                       <h3 className="text-2xl font-bold text-black dark:text-white">{stats.total_brands.toLocaleString()}</h3>
                     </div>
                     <div className="w-10 h-10 rounded-full bg-success/10 flex items-center justify-center text-success">
                       <CheckCircle className="w-5 h-5" />
                     </div>
                   </div>
                   <div className="panel p-4 flex items-center justify-between border-l-4 border-l-warning">
                     <div>
                       <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">Total Images</p>
                       <h3 className="text-2xl font-bold text-black dark:text-white">{stats.total_images.toLocaleString()}</h3>
                     </div>
                     <div className="w-10 h-10 rounded-full bg-warning/10 flex items-center justify-center text-warning">
                       <ImageIcon className="w-5 h-5" />
                     </div>
                   </div>
                 </div>
               )}

               {hasProducts && (
                 <div className="flex-none bg-white/50 dark:bg-[#0e1726]/50 border-b border-white-light dark:border-[#1b2e4b] p-4 flex gap-4 items-center">
                    <button 
                      onClick={() => { fetchProducts(); fetchStats(); }}
                      className="p-2 bg-primary/10 text-primary hover:bg-primary hover:text-white rounded-md transition-colors"
                      title="Refresh Data"
                    >
                      <RefreshCw className="w-5 h-5" />
                    </button>
                    <div className="relative flex-1 max-w-md">
                      <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#888ea8]" />
                      <input 
                        type="text" 
                        placeholder="Search products..." 
                        value={search}
                        onChange={(e) => { setSearch(e.target.value); setPage(1); }}
                        className="form-input pl-10"
                      />
                    </div>
                    <div className="relative w-64">
                      <Filter className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#888ea8]" />
                      <select 
                        value={category}
                        onChange={(e) => { setCategory(e.target.value); setPage(1); }}
                        className="form-input pl-10 appearance-none"
                      >
                        <option value="">All Categories</option>
                        {allCategories.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                    <div className="flex bg-white dark:bg-[#0e1726] border border-white-light dark:border-[#1b2e4b] rounded-md overflow-hidden p-1">
                      <button onClick={() => setListMode(false)} className={`p-1.5 rounded transition-colors ${!listMode ? 'bg-[#f4f4f4] dark:bg-[#1b2e4b] text-primary' : 'text-[#888ea8] hover:text-black dark:hover:text-white'}`}>
                        <LayoutGrid className="w-4 h-4" />
                      </button>
                      <button onClick={() => setListMode(true)} className={`p-1.5 rounded transition-colors ${listMode ? 'bg-[#f4f4f4] dark:bg-[#1b2e4b] text-primary' : 'text-[#888ea8] hover:text-black dark:hover:text-white'}`}>
                        <List className="w-4 h-4" />
                      </button>
                    </div>
                 </div>
               )}

               <div className="flex-1 overflow-y-auto p-6">
                <div className={listMode ? "flex flex-col gap-4" : "grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6"}>
                  {!hasProducts ? (
                    <div className="col-span-full py-20 flex flex-col items-center justify-center text-center">
                      <div className="w-24 h-24 bg-white dark:bg-[#0e1726] rounded-full shadow-[0_5px_20px_0_rgba(41,26,204,0.12)] flex items-center justify-center mb-4">
                        <Package className="w-10 h-10 text-primary opacity-50" />
                      </div>
                      <h3 className="text-xl font-bold text-black dark:text-white mb-2">No Products Scraped</h3>
                      <p className="text-[#888ea8]">Trigger a scrape from the top bar to populate the database.</p>
                    </div>
                  ) : products.length === 0 ? (
                    <div className="col-span-full py-20 text-center text-[#888ea8]">
                       No products matched your search.
                    </div>
                  ) : (
                    products.map((p, i) => (
                      <div key={i} className={`panel p-0 group cursor-pointer hover:-translate-y-1 transition-transform flex ${listMode ? 'flex-row items-center pr-4' : 'flex-col'}`} onClick={() => setSelectedProduct(p)}>
                        <div className={`${listMode ? 'w-32 h-32 flex-none' : 'aspect-square w-full'} bg-[#f4f4f4] dark:bg-[#1b2e4b] overflow-hidden flex items-center justify-center relative`}>
                          {p.images && p.images[0] ? (
                            <img 
                              src={`${API}/api/image?title=${encodeURIComponent(p.title)}&filename=${encodeURIComponent(p.images[0].split('/').pop())}`} 
                              className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-500"
                              alt={p.title}
                              onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'block'; }}
                            />
                          ) : null}
                          <ImageIcon className="w-12 h-12 text-[#888ea8]/30 absolute inset-0 m-auto" style={{ display: p.images && p.images[0] ? 'none' : 'block' }} />
                        </div>
                        <div className={`p-4 flex-1 flex flex-col ${listMode ? 'justify-center' : ''}`}>
                          <h4 className="font-semibold text-black dark:text-white text-[15px] mb-1 line-clamp-2 leading-tight">{p.title}</h4>
                          <div className={listMode ? "mt-1 flex items-center justify-between" : "mt-auto"}>
                            {p.price && <p className="text-secondary font-bold text-base mb-2">{p.price}</p>}
                            <div className={`flex flex-wrap gap-1 ${listMode ? 'mb-2 ml-4' : 'mt-2'}`}>
                              {p.brand && p.brand !== 'Unknown' && <span className="badge badge-outline-primary">{p.brand}</span>}
                              {p.categories?.slice(0, listMode ? 3 : 2).map((c, ci) => <span key={ci} className="badge">{c}</span>)}
                              {p.categories?.length > (listMode ? 3 : 2) && <span className="badge">+{p.categories.length - (listMode ? 3 : 2)}</span>}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
               </div>

               {totalPages > 0 && (
                 <div className="flex-none bg-white dark:bg-[#0e1726] border-t border-white-light dark:border-[#1b2e4b] p-4 flex flex-col sm:flex-row items-center justify-between gap-4">
                    <div className="flex items-center gap-2">
                      <span className="text-black dark:text-white font-semibold">Per Page:</span>
                      <select 
                        value={limit}
                        onChange={(e) => { setLimit(Number(e.target.value)); setPage(1); }}
                        className="form-input py-1 w-20 text-center"
                      >
                        <option value={20}>20</option>
                        <option value={50}>50</option>
                        <option value={100}>100</option>
                      </select>
                    </div>

                    <div className="flex items-center gap-1">
                      <button 
                        onClick={() => setPage(p => Math.max(1, p - 1))}
                        disabled={page === 1}
                        className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-primary/10 hover:text-primary disabled:opacity-50 transition-colors"
                      >
                        <ChevronLeft className="w-5 h-5" />
                      </button>
                      
                      {getPaginationGroup().map((item, index) => (
                        <button
                          key={index}
                          onClick={() => setPage(item)}
                          className={`w-8 h-8 flex items-center justify-center rounded-full font-semibold transition-colors ${page === item ? 'bg-primary text-white' : 'hover:bg-primary/10 hover:text-primary text-black dark:text-white'}`}
                        >
                          {item}
                        </button>
                      ))}

                      <button 
                        onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                        disabled={page === totalPages}
                        className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-primary/10 hover:text-primary disabled:opacity-50 transition-colors"
                      >
                        <ChevronRight className="w-5 h-5" />
                      </button>
                    </div>
                    
                    <span className="font-semibold text-[#888ea8] text-xs">Page {page} of {totalPages}</span>
                 </div>
               )}
            </div>
          )}

          {view === 'files' && (
            <div className="h-full flex flex-col p-6">
              <div className="panel flex-1 flex flex-col">
                <div className="flex items-center gap-2 mb-6 pb-4 border-b border-white-light dark:border-[#1b2e4b] text-[15px]">
                  <button onClick={() => { setCurrentPath([]); fetchFiles(); }} className="font-semibold text-primary hover:underline">data</button>
                  {currentPath.map((p, i) => (
                    <span key={i} className="flex items-center gap-2">
                      <ChevronRight className="w-4 h-4 text-[#888ea8]" />
                      <button 
                        onClick={() => {
                          const np = currentPath.slice(0, i + 1);
                          setCurrentPath(np);
                          fetchFiles(np[np.length - 1].path);
                        }} 
                        className="font-semibold text-primary hover:underline"
                      >
                        {p.name}
                      </button>
                    </span>
                  ))}
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
                  {currentPath.length > 0 && (
                    <div onClick={navigateUp} className="border border-white-light dark:border-[#1b2e4b] rounded-md p-4 cursor-pointer hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] transition-colors flex flex-col items-center justify-center gap-2 text-center h-32">
                      <Folder className="w-8 h-8 text-warning" />
                      <span className="font-semibold text-black dark:text-white">.. Go Up</span>
                    </div>
                  )}
                  {files.map((f, i) => (
                    <div key={i} onClick={() => navigateTo(f)} className="border border-white-light dark:border-[#1b2e4b] rounded-md p-4 cursor-pointer hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] transition-colors flex flex-col items-center gap-2 text-center relative h-32">
                      {f.type === 'directory' ? (
                        <Folder className="w-10 h-10 text-warning mb-1" />
                      ) : isImage(f.name) ? (
                        <img src={`${API}/data/${f.path}`} className="w-full h-16 object-cover rounded mb-1" alt={f.name} onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'block'; }} />
                      ) : (
                        <div className="w-10 h-10 bg-primary/10 rounded flex items-center justify-center text-primary font-bold text-xs mb-1">JSON</div>
                      )}
                      <span className="text-xs font-semibold text-black dark:text-white line-clamp-2 w-full break-all leading-tight mt-auto">{f.name}</span>
                    </div>
                  ))}
                  {files.length === 0 && currentPath.length === 0 && (
                    <div className="col-span-full py-10 text-center text-[#888ea8]">No files found.</div>
                  )}
                </div>
              </div>
            </div>
          )}

          {view === 'logs' && (
            <div className="h-full flex flex-col p-6">
              <div className="panel flex-1 flex flex-col">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-bold text-black dark:text-white">Terminal Output</h3>
                  <span className="badge badge-success px-3 py-1">Running</span>
                </div>
                <div className="terminal flex-1 p-4 h-full overflow-y-auto">
                  {logs.length === 0 && <div className="text-[#888ea8]">Waiting for logs...</div>}
                  {logs.map((line, i) => {
                    const isError = line.includes('ERROR');
                    const isWarn = line.includes('WARNING');
                    const isSuccess = line.includes('finished') || line.includes('complete');
                    return (
                      <div key={i} className={`py-0.5 flex gap-3 ${isError ? 'text-danger' : isWarn ? 'text-warning' : isSuccess ? 'text-success' : ''}`}>
                        <span className="opacity-50 shrink-0">{line.split(' - ')[0]}</span>
                        <span className="font-semibold shrink-0">{line.match(/- (INFO|ERROR|WARNING|DEBUG) -/)?.[1]}</span>
                        <span className="break-all">{line.split(' - ').slice(2).join(' - ') || line}</span>
                      </div>
                    )
                  })}
                  {logs.length === 0 && <div className="text-[#888ea8]">Waiting for logs...</div>}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </div>
          )}

          {view === 'status' && (
            <div className="h-full overflow-y-auto p-6">
              {(() => {
                const overallMeta = {
                  operational: { label: 'All Systems Operational', color: 'success', Icon: CheckCircle },
                  degraded: { label: 'Degraded Performance', color: 'warning', Icon: AlertTriangle },
                  down: { label: 'System Down', color: 'danger', Icon: XCircle },
                }[systemStatus.overall] || { label: 'Checking Systems…', color: 'secondary', Icon: Activity };
                const OverallIcon = overallMeta.Icon;
                const overallBoxClasses = {
                  success: 'border-l-success bg-success/10 text-success',
                  warning: 'border-l-warning bg-warning/10 text-warning',
                  danger: 'border-l-danger bg-danger/10 text-danger',
                  secondary: 'border-l-secondary bg-secondary/10 text-secondary',
                }[overallMeta.color];
                const [borderClass, ...iconBoxClasses] = overallBoxClasses.split(' ');

                return (
                  <div className={`panel p-6 mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-l-4 ${borderClass}`}>
                    <div className="flex items-center gap-4">
                      <div className={`w-14 h-14 rounded-full flex items-center justify-center flex-none ${iconBoxClasses.join(' ')}`}>
                        <OverallIcon className="w-7 h-7" />
                      </div>
                      <div>
                        <h3 className="text-xl font-bold text-black dark:text-white">{overallMeta.label}</h3>
                        <p className="text-[#888ea8] text-sm">
                          {systemStatus.checked_at ? `Last checked ${formatRelativeTime(systemStatus.checked_at)}` : 'Gathering status…'}
                        </p>
                      </div>
                    </div>
                    <button onClick={fetchSystemStatus} className="btn btn-outline-primary gap-2 self-start sm:self-auto">
                      <RefreshCw className="w-4 h-4" /> Refresh
                    </button>
                  </div>
                );
              })()}

              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                {systemStatus.services.length === 0 ? (
                  <div className="col-span-full py-16 text-center text-[#888ea8]">Loading service health…</div>
                ) : systemStatus.services.map((svc) => {
                  const Icon = STATUS_ICONS[svc.icon] || Activity;
                  const meta = SERVICE_STATUS_META[svc.status] || SERVICE_STATUS_META.warning;
                  return (
                    <div key={svc.id} className="panel p-5 flex items-start gap-4">
                      <div className={`w-11 h-11 rounded-full flex items-center justify-center flex-none ${meta.iconBox}`}>
                        <Icon className="w-5 h-5" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2 mb-1.5">
                          <div>
                            <p className="text-[10px] font-bold text-[#888ea8] uppercase tracking-wider">{svc.category}</p>
                            <h4 className="font-bold text-black dark:text-white text-[15px] leading-tight">{svc.name}</h4>
                          </div>
                          <span className={`badge shrink-0 ${meta.badge}`}>{meta.label}</span>
                        </div>
                        <p className="text-xs text-[#888ea8] leading-relaxed break-words">{svc.detail}</p>
                        {svc.checked_at && (
                          <p className="text-[10px] text-[#888ea8]/70 mt-1.5">Checked {formatRelativeTime(svc.checked_at)}</p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

        </div>
      </div>

      {/* Modal */}
      {selectedProduct && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 sm:p-6">
          <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-4xl max-h-full flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]">
            
            <div className="flex items-center justify-between px-6 py-4 border-b border-white-light dark:border-[#1b2e4b]">
              <h3 className="text-xl font-bold text-black dark:text-white pr-8 line-clamp-1">{selectedProduct.title}</h3>
              <button onClick={() => setSelectedProduct(null)} className="p-1.5 rounded-md hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] text-[#888ea8]">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="overflow-y-auto p-6 flex flex-col md:flex-row gap-8">
              <div className="w-full md:w-[40%] space-y-4 shrink-0">
                <div className="aspect-square rounded-md overflow-hidden bg-[#f4f4f4] dark:bg-[#1b2e4b] border border-white-light dark:border-[#1b2e4b]">
                  {selectedProduct.images?.[0] ? (
                    <img src={`${API}/data/images/${sanitizeName(selectedProduct.title)}/${selectedProduct.images[0].split('/').pop()}`} className="w-full h-full object-cover" alt="" />
                  ) : <div className="w-full h-full flex items-center justify-center"><ImageIcon className="w-12 h-12 opacity-20" /></div>}
                </div>
                <div className="flex gap-2 overflow-x-auto pb-2 snap-x">
                  {selectedProduct.images?.slice(1).map((img, i) => (
                    <img key={i} src={`${API}/data/images/${sanitizeName(selectedProduct.title)}/${img.split('/').pop()}`} className="w-16 h-16 rounded object-cover cursor-pointer hover:opacity-80 snap-start shrink-0 border border-white-light dark:border-[#1b2e4b]" alt="" />
                  ))}
                </div>
              </div>
              
              <div className="flex-1 space-y-6">
                <div>
                  <h4 className="text-3xl font-bold text-secondary mb-2">{selectedProduct.price}</h4>
                  <a href={selectedProduct.url} target="_blank" rel="noreferrer" className="text-primary hover:underline font-semibold text-[15px]">View on PhonePlaceKenya ↗</a>
                </div>
                
                <div>
                  <h5 className="font-bold text-black dark:text-white uppercase tracking-wider text-xs mb-3">Brand & Categories</h5>
                  <div className="flex flex-wrap gap-2">
                    {selectedProduct.brand && selectedProduct.brand !== 'Unknown' && (
                       <span className="badge badge-outline-primary">{selectedProduct.brand}</span>
                    )}
                    {selectedProduct.categories?.map((c, i) => <span key={i} className="badge">{c}</span>)}
                  </div>
                </div>

                {selectedProduct.short_description && (
                  <div>
                    <h5 className="font-bold text-black dark:text-white uppercase tracking-wider text-xs mb-3">Description</h5>
                    <div className="text-[15px] leading-relaxed text-black dark:text-[#888ea8] space-y-2" dangerouslySetInnerHTML={{ __html: selectedProduct.short_description }} />
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function sanitizeName(name) {
  return (name || '').replace(/[^a-zA-Z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
}

function isImage(name) {
  return /\.(jpg|jpeg|png|webp|gif|svg)$/i.test(name);
}

const STATUS_ICONS = {
  server: Server,
  cpu: Cpu,
  database: Database,
  layers: Layers,
  'file-text': FileText,
  globe: Globe,
  'hard-drive': HardDrive,
  activity: Activity,
};

const SERVICE_STATUS_META = {
  operational: { label: 'Operational', badge: 'badge-success', iconBox: 'bg-success/10 text-success' },
  active: { label: 'Active', badge: 'badge-success', iconBox: 'bg-success/10 text-success' },
  checking: { label: 'Checking…', badge: 'badge-secondary', iconBox: 'bg-secondary/10 text-secondary' },
  warning: { label: 'Degraded', badge: 'badge-warning', iconBox: 'bg-warning/10 text-warning' },
  down: { label: 'Down', badge: 'badge-danger', iconBox: 'bg-danger/10 text-danger' },
};

function formatRelativeTime(isoString) {
  if (!isoString) return 'just now';
  const diffMs = Date.now() - new Date(isoString).getTime();
  const seconds = Math.max(0, Math.round(diffMs / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
