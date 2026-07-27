import { useState, useEffect, useRef } from 'react';
import { Package, Folder, Terminal, Download, StopCircle, PlayCircle, Menu, Moon, Sun, Monitor, Loader2, Image as ImageIcon, CheckCircle, ChevronRight, X, Search, ChevronLeft, Filter, LayoutGrid, List, RefreshCw, Users } from 'lucide-react';
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
  
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState([]);
  const [loading, setLoading] = useState(false);
  const [scraping, setScraping] = useState(false);
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
    const interval = setInterval(() => {
      fetchLogs();
      fetchProgress();
      fetchStatus();
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

  const fetchStats = async () => {
    try {
      const res = await fetch(`${API}/api/stats`);
      const data = await res.json();
      setStats(data);
    } catch (e) {
      console.error('Failed to fetch stats', e);
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
      
      {/* Sidebar */}
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
        </div>

        <div className="mt-auto p-4 border-t border-white-light dark:border-[#1b2e4b]">
            <div className="flex flex-col gap-2 mb-4">
              <a href={`${API}/api/export/json`} download className="btn btn-outline-primary w-full gap-2">
                <Download className="w-4 h-4" /> Export JSON
              </a>
              <a href={`${API}/api/export/csv`} download className="btn btn-outline-primary w-full gap-2">
                <Download className="w-4 h-4" /> Export CSV
              </a>
              <a href={`${API}/api/export/excel`} download className="btn btn-outline-primary w-full gap-2">
                <Download className="w-4 h-4" /> Export Excel
              </a>
              <a href={`${API}/api/export/xml`} download className="btn btn-outline-primary w-full gap-2 text-left">
                <Download className="w-4 h-4" /> Export XML
              </a>
           </div>
           
           <div className="flex flex-col gap-2">
              <p className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-1">ZIP Archives</p>
              <a href={`${API}/api/export/structured`} download className="btn btn-outline-secondary w-full gap-2">
                <Download className="w-4 h-4" /> Export Everything
              </a>
              <a href={`${API}/api/export/structured/data`} download className="btn btn-outline-secondary w-full gap-2">
                <Download className="w-4 h-4" /> Export Products Only
              </a>
              <a href={`${API}/api/export/structured/images`} download className="btn btn-outline-secondary w-full gap-2">
                <Download className="w-4 h-4" /> Export Images Only
              </a>
           </div>
        </div>
      </aside>

      {/* Main Content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        
        {/* Header */}
        <header className="flex-none bg-white dark:bg-[#0e1726] border-b border-white-light dark:border-[#1b2e4b] px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-2 rounded-full hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] transition-colors text-black dark:text-white">
              <Menu className="w-5 h-5" />
            </button>
            <h1 className="text-lg font-semibold text-black dark:text-white hidden xl:block">
              {view === 'products' && 'Product Database'}
              {view === 'files' && 'Data Directory'}
              {view === 'logs' && 'Terminal Output'}
            </h1>
          </div>

          <div className="flex items-center gap-4">
             <form onSubmit={startScrape} className="flex items-center gap-2">
                <input
                  type="text"
                  placeholder="Target URL (or blank)"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={scraping}
                  className="form-input w-40 sm:w-56"
                />
                
                <div className="relative group flex items-center">
                  <div className="absolute left-3 text-[#888ea8] pointer-events-none">
                    <Users className="w-4 h-4" />
                  </div>
                  <input
                    type="number"
                    min="1" max="100"
                    title="Concurrent Workers"
                    value={workers}
                    onChange={(e) => setWorkers(parseInt(e.target.value) || 1)}
                    disabled={scraping}
                    className="form-input w-24 pl-9 text-center"
                  />
                  <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-max px-2 py-1 bg-black dark:bg-[#0e1726] border border-white-light dark:border-[#1b2e4b] shadow-lg text-white text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                    Concurrent Workers
                  </div>
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

        {/* Progress Bar (if scraping or completed) */}
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

        {/* Dynamic View Content */}
        <div className="flex-1 overflow-hidden flex flex-col">
          
          {/* PRODUCTS VIEW */}
          {view === 'products' && (
            <div className="flex-1 flex flex-col h-full overflow-hidden">
               {/* Metrics Dashboard */}
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

               {/* Search & Filter Bar */}
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

               {/* Pagination Controls */}
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

          {/* FILES VIEW */}
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

          {/* LOGS VIEW */}
          {view === 'logs' && (
            <div className="h-full flex flex-col p-6">
              <div className="panel flex-1 flex flex-col">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-bold text-black dark:text-white">Terminal Output</h3>
                  <span className="badge badge-success px-3 py-1">Running</span>
                </div>
                <div className="terminal flex-1 min-h-0 p-4">
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
