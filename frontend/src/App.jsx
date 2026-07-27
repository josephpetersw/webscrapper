import { useState, useEffect, useRef } from 'react';
import { Package, Folder, Terminal, Download, StopCircle, PlayCircle, Menu, Moon, Sun, Monitor, Loader2, Image as ImageIcon, CheckCircle, ChevronRight, X, Search, ChevronLeft, Filter, LayoutGrid, List, RefreshCw, Users, Activity, Server, Database, HardDrive, Globe, Cpu, FileText, Layers, AlertTriangle, XCircle, ExternalLink, FileJson2, FileType, Trash2 } from 'lucide-react';
import { marked } from 'marked';
import './index.css';

const API = 'http://localhost:5000';

export default function App() {
  const [url, setUrl] = useState('');
  const [workers, setWorkers] = useState(8);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [logs, setLogs] = useState([]);
  const [progress, setProgress] = useState({ current: 0, total: 0, eta: 0 });
  const [products, setProducts] = useState([]);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [previewFile, setPreviewFile] = useState(null);
  const [previewContent, setPreviewContent] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  
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
  const [sites, setSites] = useState([]);
  const [activeSite, setActiveSite] = useState(null);
  const [siteCheck, setSiteCheck] = useState(null);
  const [siteAnalysis, setSiteAnalysis] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [scrapeMode, setScrapeMode] = useState('update');
  const [wipeOpen, setWipeOpen] = useState(false);
  const [wiping, setWiping] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportSites, setExportSites] = useState([]);
  const [exportFormats, setExportFormats] = useState(['csv']);
  const [exportClean, setExportClean] = useState(true);
  const [exportJob, setExportJob] = useState(null);
  const [selectedSites, setSelectedSites] = useState([]);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const exportPollRef = useRef(null);
  
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
    fetchSites();
    const interval = setInterval(() => {
      fetchLogs();
      fetchProgress();
      fetchStatus();
      fetchSystemStatus();
      fetchSites();
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

  const fetchSites = async () => {
    try {
      const res = await fetch(`${API}/api/sites`);
      const data = await res.json();
      setSites(data.sites || []);
      setActiveSite(data.active || null);
    } catch {}
  };

  const switchSite = async (name) => {
    try {
      await fetch(`${API}/api/sites/active`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      setActiveSite(name);
      setPage(1);
      fetchProducts();
      fetchStats();
      fetchFiles();
      setCurrentPath([]);
    } catch {}
  };

  const deleteSites = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const res = await fetch(`${API}/api/sites/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ names: deleteTarget.names }),
      });
      const data = await res.json();
      if (data.status === 'success') {
        setDeleteTarget(null);
        setSelectedSites([]);
        setProducts([]);
        setCurrentPath([]);
        await Promise.all([fetchSites(), fetchFiles(), fetchStats(), fetchSystemStatus()]);
      } else {
        setDeleteTarget({ ...deleteTarget, error: data.message || 'Could not delete.' });
      }
    } catch (e) {
      setDeleteTarget({ ...deleteTarget, error: e.message });
    }
    setDeleting(false);
  };

  const wipeEverything = async () => {
    setWiping(true);
    try {
      const res = await fetch(`${API}/api/system/wipe`, { method: 'POST' });
      const data = await res.json();
      if (data.status === 'success') {
        setWipeOpen(false);
        setProducts([]);
        setStats({ total_products: 0, total_categories: 0, total_brands: 0, total_images: 0 });
        setProgress({ current: 0, total: 0, eta: 0 });
        setLogs([]);
        setCurrentPath([]);
        setSiteCheck(null);
        await Promise.all([fetchSites(), fetchFiles(), fetchSystemStatus()]);
      }
    } catch {}
    setWiping(false);
  };

  const openExport = () => {
    // Default to the site you're already looking at.
    setExportSites(activeSite ? [activeSite] : sites.slice(0, 1).map(s => s.name));
    setExportFormats(['csv']);
    setExportClean(true);
    setExportOpen(true);
  };

  const toggleIn = (list, value) =>
    list.includes(value) ? list.filter(v => v !== value) : [...list, value];

  // Exports run as a background job on the server: a full image archive is
  // thousands of files and far too slow to hold a request open for.
  const runBundleExport = async () => {
    setExportOpen(false);
    setExportJob({ state: 'queued', step: 0, total_steps: exportSites.length * exportFormats.length,
                   message: 'Starting export…' });
    try {
      const res = await fetch(`${API}/api/export/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sites: exportSites, formats: exportFormats, clean: exportClean }),
      });
      const started = await res.json();
      if (!res.ok) throw new Error(started.error || 'Could not start export');
      pollExportJob(started.job_id);
    } catch (e) {
      setExportJob({ state: 'error', error: e.message, message: 'Export failed' });
    }
  };

  const pollExportJob = (jobId) => {
    clearInterval(exportPollRef.current);
    exportPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/export/status/${jobId}`);
        const job = await res.json();
        if (!res.ok) throw new Error(job.error || 'Export job lost');
        setExportJob({ ...job, jobId });
        if (job.state === 'ready' || job.state === 'error') {
          clearInterval(exportPollRef.current);
        }
      } catch (e) {
        clearInterval(exportPollRef.current);
        setExportJob({ state: 'error', error: e.message, message: 'Export failed' });
      }
    }, 700);
  };

  const downloadExportJob = () => {
    if (!exportJob?.jobId) return;
    // Plain navigation: the browser streams a multi-hundred-MB file straight
    // to disk instead of buffering it in memory as a blob.
    window.location.href = `${API}/api/export/download/${exportJob.jobId}`;
    setExportJob(null);
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

  // Submitting the form only asks for confirmation — a scrape is a long, heavy
  // job, and pressing Enter in the URL box used to launch one instantly.
  const requestScrape = async (e) => {
    e.preventDefault();
    if (scraping || loading) return;
    setSiteCheck(null);
    setSiteAnalysis(null);
    setAnalyzing(true);
    setScrapeMode('update');
    setConfirmOpen(true);
    try {
      const res = await fetch(`${API}/api/site/check?url=${encodeURIComponent(url)}`);
      const check = await res.json();
      setSiteCheck(check);
      if (check.valid) {
        const ares = await fetch(`${API}/api/site/analyze?url=${encodeURIComponent(url)}`);
        setSiteAnalysis(await ares.json());
      }
    } catch {
      setSiteCheck({ valid: false, message: 'Could not reach the backend.' });
    }
    setAnalyzing(false);
  };

  const startScrape = async () => {
    setConfirmOpen(false);
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: url,
          workers: workers,
          new_version: scrapeMode === 'new_version',
        })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setUrl('');
        setScraping(true);
        fetchSites();
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
      openPreview(node);
    }
  };

  const openPreview = async (node) => {
    setPreviewFile(node);
    setPreviewContent(null);
    setPreviewError(null);
    const kind = getFileKind(node.name);
    if (kind === 'image') return;
    setPreviewLoading(true);
    try {
      const res = await fetch(`${API}/data/${node.path}`);
      if (!res.ok) throw new Error(`Failed to load file (HTTP ${res.status})`);
      setPreviewContent(await res.text());
    } catch (e) {
      setPreviewError(e.message || 'Failed to load file');
    }
    setPreviewLoading(false);
  };

  const closePreview = () => {
    setPreviewFile(null);
    setPreviewContent(null);
    setPreviewError(null);
  };

  const navigateUp = () => {
    const newPath = currentPath.slice(0, -1);
    setCurrentPath(newPath);
    fetchFiles(newPath.length > 0 ? newPath[newPath.length - 1].path : '');
  };

  const activeSiteInfo = sites.find(s => s.name === activeSite) || null;
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
          <button
            onClick={openExport}
            disabled={sites.length === 0}
            className="btn btn-outline-primary w-full gap-2 justify-center disabled:opacity-40 disabled:cursor-not-allowed"
            title={sites.length === 0 ? 'Scrape a site first' : 'Export scraped data'}
          >
            <Download className="w-4 h-4" /> Export Data
          </button>
          <p className="text-[11px] text-[#888ea8] text-center mt-2 leading-relaxed">
            {sites.length === 0
              ? 'Nothing to export yet'
              : `${sites.length} site${sites.length === 1 ? '' : 's'} available`}
          </p>
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

            {activeSiteInfo && (
              <div className="hidden md:flex items-center gap-2.5 pl-4 border-l border-white-light dark:border-[#1b2e4b] min-w-0">
                <Globe className="w-4 h-4 text-primary flex-none" />
                <div className="min-w-0">
                  <p className="font-bold text-black dark:text-white text-sm leading-tight truncate max-w-[240px]">
                    {activeSiteInfo.name}
                  </p>
                  <p className="text-[11px] text-[#888ea8] leading-tight">
                    Last scraped {formatRelativeTime(activeSiteInfo.modified)}
                  </p>
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-4 flex-1 justify-end ml-4">
             <form onSubmit={requestScrape} className="flex items-center gap-2 flex-1 max-w-4xl justify-end">
                <input
                  type="text"
                  placeholder="Store URL — the whole catalogue is discovered and crawled"
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
               {activeSiteInfo && (
                 <div className="p-4 pb-0 bg-[#fafafa] dark:bg-[#060818]">
                   <div className="panel p-5 flex flex-col lg:flex-row lg:items-center justify-between gap-5 border-l-4 border-l-primary">
                     <div className="flex items-center gap-4 min-w-0">
                       <div className="w-12 h-12 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-none">
                         <Globe className="w-6 h-6" />
                       </div>
                       <div className="min-w-0">
                         <p className="text-[10px] font-bold text-[#888ea8] uppercase tracking-wider mb-0.5">Currently viewing</p>
                         <h3 className="text-xl font-bold text-black dark:text-white leading-tight truncate">
                           {activeSiteInfo.name}
                         </h3>
                         <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-xs text-[#888ea8]">
                           <span className="flex items-center gap-1.5">
                             <Package className="w-3.5 h-3.5" />
                             {activeSiteInfo.products.toLocaleString()} products
                           </span>
                           <span className="flex items-center gap-1.5">
                             <RefreshCw className="w-3.5 h-3.5" />
                             Last scraped {formatRelativeTime(activeSiteInfo.modified)}
                           </span>
                           {activeSiteInfo.failed > 0 && (
                             <span className="flex items-center gap-1.5 text-warning font-semibold">
                               <AlertTriangle className="w-3.5 h-3.5" />
                               {activeSiteInfo.failed.toLocaleString()} failed — re-run to retry
                             </span>
                           )}
                         </div>
                       </div>
                     </div>

                     {sites.length > 1 && (
                       <div className="flex-none lg:text-right">
                         <label className="block text-[10px] font-bold text-[#888ea8] uppercase tracking-wider mb-1.5">
                           Switch to another scrape
                         </label>
                         <div className="relative flex items-center bg-white dark:bg-[#121e32] border border-white-light dark:border-[#17263c] rounded-md h-[38px] pl-3">
                           <Layers className="w-4 h-4 text-[#888ea8] flex-none" />
                           <select
                             value={activeSite || ''}
                             onChange={(e) => switchSite(e.target.value)}
                             className="bg-transparent text-black dark:text-white font-semibold text-sm outline-none px-2 pr-3 cursor-pointer w-full lg:min-w-[260px]"
                           >
                             {sites.map(s => (
                               <option key={s.name} value={s.name}>
                                 {s.name} — {s.products.toLocaleString()} products
                               </option>
                             ))}
                           </select>
                         </div>
                       </div>
                     )}
                   </div>
                 </div>
               )}

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
                  {files.map((f, i) => {
                    const kind = f.type === 'directory' ? null : getFileKind(f.name);
                    const meta = kind ? FILE_KIND_META[kind] : null;
                    const KindIcon = meta?.Icon;
                    return (
                      <div key={i} onClick={() => navigateTo(f)} className="border border-white-light dark:border-[#1b2e4b] rounded-md p-4 cursor-pointer hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] transition-colors flex flex-col items-center gap-2 text-center relative h-32">
                        {f.type === 'directory' ? (
                          <Folder className="w-10 h-10 text-warning mb-1" />
                        ) : kind === 'image' ? (
                          <img src={`${API}/data/${f.path}`} className="w-full h-16 object-cover rounded mb-1" alt={f.name} onError={(e) => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'block'; }} />
                        ) : (
                          <div className={`w-10 h-10 rounded flex items-center justify-center mb-1 ${meta.iconBox}`}>
                            <KindIcon className="w-5 h-5" />
                          </div>
                        )}
                        <span className="text-xs font-semibold text-black dark:text-white line-clamp-2 w-full break-all leading-tight mt-auto">{f.name}</span>
                      </div>
                    );
                  })}
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
                    <div className="flex gap-2 self-start sm:self-auto">
                      <button onClick={fetchSystemStatus} className="btn btn-outline-primary gap-2">
                        <RefreshCw className="w-4 h-4" /> Refresh
                      </button>
                      <button onClick={() => setWipeOpen(true)} className="btn btn-outline-danger gap-2">
                        <Trash2 className="w-4 h-4" /> Wipe All Data
                      </button>
                    </div>
                  </div>
                );
              })()}

              {sites.length > 0 && (
                <div className="panel p-0 mb-6 overflow-hidden">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-5 py-4 border-b border-white-light dark:border-[#1b2e4b]">
                    <div>
                      <h4 className="font-bold text-black dark:text-white">Scraped sites</h4>
                      <p className="text-xs text-[#888ea8]">
                        {sites.length} site{sites.length === 1 ? '' : 's'} stored ·{' '}
                        {sites.reduce((n, s) => n + s.products, 0).toLocaleString()} products total
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => setSelectedSites(selectedSites.length === sites.length ? [] : sites.map(s => s.name))}
                        className="btn btn-outline-secondary py-1.5 px-3 text-xs">
                        {selectedSites.length === sites.length ? 'Clear selection' : 'Select all'}
                      </button>
                      <button
                        onClick={() => setDeleteTarget({ names: selectedSites })}
                        disabled={selectedSites.length === 0}
                        className="btn btn-outline-danger py-1.5 px-3 text-xs gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed">
                        <Trash2 className="w-3.5 h-3.5" />
                        Delete selected{selectedSites.length > 0 ? ` (${selectedSites.length})` : ''}
                      </button>
                    </div>
                  </div>

                  <div className="divide-y divide-white-light dark:divide-[#1b2e4b]">
                    {sites.map(s => {
                      const picked = selectedSites.includes(s.name);
                      return (
                        <div key={s.name} className={`flex items-center gap-3 px-5 py-3 transition-colors ${picked ? 'bg-primary/5' : ''}`}>
                          <input type="checkbox" checked={picked}
                            onChange={() => setSelectedSites(toggleIn(selectedSites, s.name))}
                            className="accent-[#4361ee] flex-none" />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                              <span className="font-semibold text-black dark:text-white text-sm truncate">{s.name}</span>
                              {s.active && <span className="badge badge-success flex-none">viewing</span>}
                            </div>
                            <p className="text-xs text-[#888ea8]">
                              {s.products.toLocaleString()} products · scraped {formatRelativeTime(s.modified)}
                              {s.failed > 0 && <span className="text-warning"> · {s.failed.toLocaleString()} failed</span>}
                            </p>
                          </div>
                          <button onClick={() => setDeleteTarget({ names: [s.name] })}
                            title={`Delete ${s.name}`}
                            className="p-2 rounded-md text-[#888ea8] hover:bg-danger/10 hover:text-danger transition-colors flex-none">
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

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
                  <a href={selectedProduct.url} target="_blank" rel="noreferrer" className="text-primary hover:underline font-semibold text-[15px]">View on store ↗</a>
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

      {/* Export progress */}
      {exportJob && (() => {
        const done = exportJob.state === 'ready';
        const failed = exportJob.state === 'error';
        const pct = exportJob.total_steps
          ? Math.min(100, Math.round(((exportJob.step || 0) / exportJob.total_steps) * 100)) : 0;
        return (
          <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4">
            <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-md flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]">
              <div className="flex items-center gap-3 px-6 py-4 border-b border-white-light dark:border-[#1b2e4b]">
                <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-none ${
                  failed ? 'bg-danger/10 text-danger' : done ? 'bg-success/10 text-success' : 'bg-primary/10 text-primary'}`}>
                  {failed ? <XCircle className="w-5 h-5" />
                    : done ? <CheckCircle className="w-5 h-5" />
                    : <Loader2 className="w-5 h-5 animate-spin" />}
                </div>
                <h3 className="text-lg font-bold text-black dark:text-white">
                  {failed ? 'Export failed' : done ? 'Export ready' : 'Preparing export'}
                </h3>
              </div>

              <div className="p-6 space-y-4">
                {!failed && (
                  <>
                    <div>
                      <div className="flex justify-between items-baseline mb-2">
                        <span className="text-sm font-semibold text-black dark:text-white">
                          {done ? 'Complete' : `Step ${Math.min((exportJob.step || 0) + 1, exportJob.total_steps || 1)} of ${exportJob.total_steps || 1}`}
                        </span>
                        <span className="text-sm font-bold text-primary">{done ? 100 : pct}%</span>
                      </div>
                      <div className="w-full h-2 bg-[#ebebeb] dark:bg-[#1b2e4b] rounded-full overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-300 ${done ? 'bg-success' : 'bg-primary'}`}
                             style={{ width: `${done ? 100 : Math.max(pct, 4)}%` }} />
                      </div>
                    </div>
                    <p className="text-sm text-[#888ea8] break-words">{exportJob.message}</p>
                  </>
                )}

                {done && (
                  <div className="rounded-md border border-white-light dark:border-[#1b2e4b] p-4 space-y-1">
                    <p className="font-mono text-sm text-black dark:text-white break-all">{exportJob.filename}</p>
                    {exportJob.size != null && (
                      <p className="text-xs text-[#888ea8]">{(exportJob.size / 1048576).toFixed(1)} MB</p>
                    )}
                  </div>
                )}

                {failed && <p className="text-sm text-danger break-words">{exportJob.error || exportJob.message}</p>}

                {!done && !failed && (
                  <p className="text-xs text-[#888ea8] leading-relaxed">
                    Large exports with images can take a few minutes. You can leave this open —
                    the work continues on the server.
                  </p>
                )}
              </div>

              <div className="flex justify-end gap-2 px-6 py-4 border-t border-white-light dark:border-[#1b2e4b]">
                <button onClick={() => { clearInterval(exportPollRef.current); setExportJob(null); }}
                  className="btn btn-outline-secondary">
                  {done || failed ? 'Close' : 'Hide'}
                </button>
                {done && (
                  <button onClick={downloadExportJob} className="btn btn-primary gap-2">
                    <Download className="w-4 h-4" /> Download
                  </button>
                )}
              </div>
            </div>
          </div>
        );
      })()}

      {/* Delete sites */}
      {deleteTarget && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => !deleting && setDeleteTarget(null)}>
          <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-lg flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-3 px-6 py-4 border-b border-white-light dark:border-[#1b2e4b]">
              <div className="w-10 h-10 rounded-full bg-danger/10 text-danger flex items-center justify-center flex-none">
                <Trash2 className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-bold text-black dark:text-white">
                Delete {deleteTarget.names.length === 1 ? 'this scrape' : `${deleteTarget.names.length} scrapes`}?
              </h3>
            </div>

            <div className="p-6 space-y-4">
              <p className="text-[#888ea8] leading-relaxed">
                This permanently deletes the products, images and files for
                {deleteTarget.names.length === 1 ? ' this scrape' : ' these scrapes'}. It cannot be undone.
              </p>
              <div className="rounded-md border border-danger/30 bg-danger/5 p-4 space-y-1.5 max-h-52 overflow-y-auto">
                {deleteTarget.names.map(name => {
                  const info = sites.find(s => s.name === name);
                  return (
                    <div key={name} className="flex items-center justify-between gap-4 text-sm">
                      <span className="font-mono text-black dark:text-white truncate">{name}</span>
                      <span className="text-[#888ea8] flex-none">{(info?.products ?? 0).toLocaleString()} products</span>
                    </div>
                  );
                })}
              </div>
              {deleteTarget.error && <p className="text-sm text-danger font-semibold">{deleteTarget.error}</p>}
            </div>

            <div className="flex justify-end gap-2 px-6 py-4 border-t border-white-light dark:border-[#1b2e4b]">
              <button onClick={() => setDeleteTarget(null)} disabled={deleting} className="btn btn-outline-secondary">Cancel</button>
              <button onClick={deleteSites} disabled={deleting} className="btn btn-danger gap-2 disabled:opacity-50">
                {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Export */}
      {exportOpen && (() => {
        const totalProducts = sites.filter(s => exportSites.includes(s.name))
                                   .reduce((sum, s) => sum + s.products, 0);
        const hasTabular = exportFormats.some(f => ['json', 'csv', 'excel', 'xml'].includes(f));
        const isZip = exportSites.length > 1 || exportFormats.length > 1
                      || exportFormats.some(f => f.startsWith('archive_'));
        return (
          <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => setExportOpen(false)}>
            <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-3xl max-h-full flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-white-light dark:border-[#1b2e4b]">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-none">
                    <Download className="w-5 h-5" />
                  </div>
                  <h3 className="text-lg font-bold text-black dark:text-white">Export data</h3>
                </div>
                <button onClick={() => setExportOpen(false)} className="p-1.5 rounded-md hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] text-[#888ea8]">
                  <X className="w-5 h-5" />
                </button>
              </div>

              <div className="overflow-y-auto p-6 space-y-6">
                <section>
                  <div className="flex items-center justify-between mb-3">
                    <h4 className="text-xs font-bold text-[#888ea8] uppercase tracking-wider">
                      <span className="text-primary">1.</span> Choose sites
                    </h4>
                    {sites.length > 1 && (
                      <button
                        onClick={() => setExportSites(exportSites.length === sites.length ? [] : sites.map(s => s.name))}
                        className="text-xs font-semibold text-primary hover:underline">
                        {exportSites.length === sites.length ? 'Clear all' : 'Select all'}
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {sites.map(s => {
                      const picked = exportSites.includes(s.name);
                      return (
                        <label key={s.name}
                          className={`flex items-center gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                            picked ? 'border-primary bg-primary/5' : 'border-white-light dark:border-[#1b2e4b] hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b]'}`}>
                          <input type="checkbox" checked={picked}
                            onChange={() => setExportSites(toggleIn(exportSites, s.name))}
                            className="accent-[#4361ee] flex-none" />
                          <span className="min-w-0 flex-1">
                            <span className="block font-semibold text-black dark:text-white text-sm truncate">{s.name}</span>
                            <span className="block text-xs text-[#888ea8]">
                              {s.products.toLocaleString()} products
                              {s.failed > 0 && <span className="text-warning"> · {s.failed} failed</span>}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </section>

                <section>
                  <h4 className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-3">
                    <span className="text-primary">2.</span> Choose what to export
                  </h4>
                  <div className="space-y-4">
                    {EXPORT_GROUPS.map(group => (
                      <div key={group.title}>
                        <p className="text-[11px] font-bold text-[#888ea8] uppercase tracking-wider mb-2">{group.title}</p>
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                          {group.items.map(item => {
                            const picked = exportFormats.includes(item.id);
                            const Icon = item.Icon;
                            return (
                              <button key={item.id}
                                onClick={() => setExportFormats(toggleIn(exportFormats, item.id))}
                                className={`flex items-start gap-2.5 p-3 rounded-md border text-left transition-colors ${
                                  picked ? 'border-primary bg-primary/5' : 'border-white-light dark:border-[#1b2e4b] hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b]'}`}>
                                <Icon className={`w-4 h-4 flex-none mt-0.5 ${picked ? 'text-primary' : 'text-[#888ea8]'}`} />
                                <span className="min-w-0">
                                  <span className="block font-semibold text-black dark:text-white text-sm leading-tight">{item.label}</span>
                                  <span className="block text-[11px] text-[#888ea8] leading-snug mt-0.5">{item.desc}</span>
                                </span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>

                {hasTabular && (
                  <section>
                    <h4 className="text-xs font-bold text-[#888ea8] uppercase tracking-wider mb-3">
                      <span className="text-primary">3.</span> Description formatting
                    </h4>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {[
                        { v: true, t: 'Clean text', d: 'HTML tags stripped — best for spreadsheets.' },
                        { v: false, t: 'Raw HTML', d: 'Original markup preserved — best for re-import.' },
                      ].map(o => (
                        <label key={String(o.v)}
                          className={`flex gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                            exportClean === o.v ? 'border-primary bg-primary/5' : 'border-white-light dark:border-[#1b2e4b] hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b]'}`}>
                          <input type="radio" name="exportClean" checked={exportClean === o.v}
                            onChange={() => setExportClean(o.v)} className="mt-1 accent-[#4361ee] flex-none" />
                          <span>
                            <span className="block font-semibold text-black dark:text-white text-sm">{o.t}</span>
                            <span className="block text-[11px] text-[#888ea8] leading-snug">{o.d}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  </section>
                )}
              </div>

              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-6 py-4 border-t border-white-light dark:border-[#1b2e4b]">
                <p className="text-xs text-[#888ea8]">
                  {exportSites.length === 0 || exportFormats.length === 0 ? (
                    <span className="text-warning font-semibold">Pick at least one site and one format.</span>
                  ) : (
                    <>
                      <span className="font-semibold text-black dark:text-white">
                        {exportFormats.length} format{exportFormats.length === 1 ? '' : 's'}
                      </span>{' '}from{' '}
                      <span className="font-semibold text-black dark:text-white">
                        {exportSites.length} site{exportSites.length === 1 ? '' : 's'}
                      </span>{' '}({totalProducts.toLocaleString()} products) · downloads as {isZip ? 'a ZIP' : 'a single file'}
                    </>
                  )}
                </p>
                <div className="flex justify-end gap-2">
                  <button onClick={() => setExportOpen(false)} className="btn btn-outline-secondary">Cancel</button>
                  <button onClick={runBundleExport}
                    disabled={exporting !== null || exportSites.length === 0 || exportFormats.length === 0}
                    className="btn btn-primary gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
                    {exporting === 'bundle' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                    {exporting === 'bundle' ? 'Preparing…' : 'Download'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Wipe Everything */}
      {wipeOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => !wiping && setWipeOpen(false)}>
          <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-lg flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-3 px-6 py-4 border-b border-white-light dark:border-[#1b2e4b]">
              <div className="w-10 h-10 rounded-full bg-danger/10 text-danger flex items-center justify-center flex-none">
                <Trash2 className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-bold text-black dark:text-white">Wipe all data?</h3>
            </div>

            <div className="p-6 space-y-4">
              <p className="text-[#888ea8] leading-relaxed">
                This permanently deletes <strong className="text-black dark:text-white">every site
                you have scraped</strong> — all products, images, markdown, exports, cached data
                and the scraper log. It cannot be undone.
              </p>

              {sites.length > 0 && (
                <div className="rounded-md border border-danger/30 bg-danger/5 p-4 space-y-1.5">
                  <p className="text-xs font-bold text-danger uppercase tracking-wider mb-2">
                    {sites.length} site{sites.length === 1 ? '' : 's'} will be deleted
                  </p>
                  {sites.map(s => (
                    <div key={s.name} className="flex items-center justify-between gap-4 text-sm">
                      <span className="font-mono text-black dark:text-white truncate">{s.name}</span>
                      <span className="text-[#888ea8] flex-none">{s.products.toLocaleString()} products</span>
                    </div>
                  ))}
                </div>
              )}

              {sites.length === 0 && (
                <p className="text-sm text-[#888ea8]">There is no scraped data to delete right now.</p>
              )}

              <p className="text-xs text-[#888ea8]">
                Export anything you want to keep before continuing.
              </p>
            </div>

            <div className="flex justify-end gap-2 px-6 py-4 border-t border-white-light dark:border-[#1b2e4b]">
              <button onClick={() => setWipeOpen(false)} disabled={wiping} className="btn btn-outline-secondary">Cancel</button>
              <button onClick={wipeEverything} disabled={wiping} className="btn btn-danger gap-2 disabled:opacity-50">
                {wiping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                {wiping ? 'Wiping…' : 'Delete Everything'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Scrape Confirmation */}
      {confirmOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => setConfirmOpen(false)}>
          <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-lg flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-3 px-6 py-4 border-b border-white-light dark:border-[#1b2e4b]">
              <div className="w-10 h-10 rounded-full bg-warning/10 text-warning flex items-center justify-center flex-none">
                <AlertTriangle className="w-5 h-5" />
              </div>
              <h3 className="text-lg font-bold text-black dark:text-white">Start scraping?</h3>
            </div>

            <div className="p-6 space-y-4">
              <p className="text-[#888ea8] leading-relaxed">
                The entire product catalogue of the target store will be discovered via its
                sitemaps and downloaded, including images. This can take a while and makes
                many requests to the site.
              </p>

              <div className="rounded-md border border-white-light dark:border-[#1b2e4b] divide-y divide-white-light dark:divide-[#1b2e4b]">
                <div className="flex items-start justify-between gap-4 px-4 py-3">
                  <span className="text-xs font-bold text-[#888ea8] uppercase tracking-wider flex-none pt-0.5">Store</span>
                  <span className="text-black dark:text-white font-semibold text-sm text-right break-all">
                    {url || <span className="text-danger">No URL entered</span>}
                  </span>
                </div>
                {siteCheck?.valid && (
                  <div className="flex items-center justify-between gap-4 px-4 py-3">
                    <span className="text-xs font-bold text-[#888ea8] uppercase tracking-wider">Saves to</span>
                    <span className="text-black dark:text-white font-semibold text-sm font-mono">
                      data/{scrapeMode === 'new_version' ? `${siteCheck.site}_v${siteCheck.versions + 2}_…` : siteCheck.site}
                    </span>
                  </div>
                )}
                <div className="flex items-center justify-between gap-4 px-4 py-3">
                  <span className="text-xs font-bold text-[#888ea8] uppercase tracking-wider">Concurrent workers</span>
                  <span className="text-black dark:text-white font-semibold text-sm">{workers}</span>
                </div>
              </div>

              {siteCheck && !siteCheck.valid && (
                <p className="text-sm text-danger font-semibold">{siteCheck.message}</p>
              )}

              {analyzing && (
                <div className="flex items-center gap-3 text-[#888ea8] py-3 px-4 rounded-md border border-white-light dark:border-[#1b2e4b]">
                  <Loader2 className="w-4 h-4 animate-spin flex-none" />
                  <span className="text-sm">Identifying the store platform…</span>
                </div>
              )}

              {siteAnalysis && !analyzing && (() => {
                const p = siteAnalysis.platform || {};
                const tone = !siteAnalysis.reachable ? 'danger' : p.supported ? 'success' : 'warning';
                const toneBox = { success: 'bg-success/10 text-success', warning: 'bg-warning/10 text-warning', danger: 'bg-danger/10 text-danger' }[tone];
                const PIcon = !siteAnalysis.reachable ? XCircle : p.supported ? CheckCircle : AlertTriangle;
                const wp = siteAnalysis.wordpress;
                return (
                  <div className="rounded-md border border-white-light dark:border-[#1b2e4b] overflow-hidden">
                    <div className="flex items-center gap-3 px-4 py-3 border-b border-white-light dark:border-[#1b2e4b]">
                      <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-none ${toneBox}`}>
                        <PIcon className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-[10px] font-bold text-[#888ea8] uppercase tracking-wider">Detected platform</p>
                        <p className="font-bold text-black dark:text-white text-[15px] leading-tight">{p.name || 'Unknown'}</p>
                      </div>
                      {p.confidence && p.confidence !== 'none' && (
                        <span className={`badge ml-auto flex-none ${p.supported ? 'badge-success' : 'badge-warning'}`}>
                          {p.confidence} confidence
                        </span>
                      )}
                    </div>

                    <div className="px-4 py-3 space-y-3 text-xs">
                      {p.evidence?.length > 0 && (
                        <div>
                          <p className="font-bold text-[#888ea8] uppercase tracking-wider mb-1.5">Identified by</p>
                          <ul className="space-y-1">
                            {p.evidence.map((ev, i) => (
                              <li key={i} className="flex gap-2 text-black dark:text-[#c5d0e6]">
                                <CheckCircle className="w-3.5 h-3.5 text-success flex-none mt-0.5" />{ev}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {wp && (
                        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                          {wp.version && <Detail label="WordPress" value={wp.version} />}
                          {wp.themes?.length > 0 && <Detail label="Theme" value={wp.themes.join(', ')} />}
                          <Detail label="Plugins found" value={`${wp.plugin_count}`} />
                        </div>
                      )}

                      {wp?.plugins?.length > 0 && (
                        <div>
                          <p className="font-bold text-[#888ea8] uppercase tracking-wider mb-1.5">Plugins</p>
                          <div className="flex flex-wrap gap-1">
                            {wp.plugins.slice(0, 12).map(pl => <span key={pl} className="badge">{pl}</span>)}
                            {wp.plugins.length > 12 && <span className="badge">+{wp.plugins.length - 12} more</span>}
                          </div>
                        </div>
                      )}

                      <div className="grid grid-cols-2 gap-x-4 gap-y-2 pt-1">
                        <Detail label="robots.txt" value={siteAnalysis.robots_txt ? 'Found' : 'Not found'} ok={siteAnalysis.robots_txt} />
                        <Detail label="Sitemaps" value={siteAnalysis.sitemaps?.length ? `${siteAnalysis.sitemaps.length} indexed` : 'None'} ok={!!siteAnalysis.sitemaps?.length} />
                        <Detail label="Product sitemaps" value={siteAnalysis.product_sitemaps?.length ? `${siteAnalysis.product_sitemaps.length} found` : 'None'} ok={!!siteAnalysis.product_sitemaps?.length} />
                        <Detail label="Discovery method" value={DISCOVERY_LABELS[siteAnalysis.strategy] || 'Listing crawl'} />
                      </div>

                      {siteAnalysis.apis?.length > 0 && (
                        <div>
                          <p className="font-bold text-[#888ea8] uppercase tracking-wider mb-1.5">APIs available</p>
                          {siteAnalysis.apis.map((a, i) => (
                            <div key={i} className="flex items-center justify-between gap-3 text-black dark:text-[#c5d0e6]">
                              <span className="flex gap-2"><CheckCircle className="w-3.5 h-3.5 text-success flex-none mt-0.5" />{a.name}</span>
                              {a.products != null && <span className="font-semibold flex-none">{a.products.toLocaleString()} products</span>}
                            </div>
                          ))}
                        </div>
                      )}

                      {siteAnalysis.estimated_products != null && (
                        <div className="flex items-center justify-between pt-2 border-t border-white-light dark:border-[#1b2e4b]">
                          <span className="font-bold text-[#888ea8] uppercase tracking-wider">Products to scrape</span>
                          <span className="text-lg font-bold text-primary">{siteAnalysis.estimated_products.toLocaleString()}</span>
                        </div>
                      )}

                      {siteAnalysis.warnings?.map((w, i) => (
                        <p key={i} className="flex gap-2 text-warning leading-relaxed">
                          <AlertTriangle className="w-3.5 h-3.5 flex-none mt-0.5" />{w}
                        </p>
                      ))}
                      {siteAnalysis.notes?.map((n, i) => (
                        <p key={i} className="flex gap-2 text-success leading-relaxed">
                          <CheckCircle className="w-3.5 h-3.5 flex-none mt-0.5" />{n}
                        </p>
                      ))}
                    </div>
                  </div>
                );
              })()}

              {siteCheck?.valid && siteCheck.exists && (
                <div className="space-y-2">
                  <p className="text-sm text-black dark:text-white font-semibold">
                    You've already scraped <span className="font-mono">{siteCheck.site}</span>
                    {' '}({siteCheck.products.toLocaleString()} products). What should happen?
                  </p>
                  {[
                    { id: 'update', title: 'Update the existing data',
                      desc: 'Keeps what you have and fills in anything missing or previously failed.' },
                    { id: 'new_version', title: 'Save as a new version',
                      desc: 'Leaves the current data untouched and scrapes into a new timestamped folder.' },
                  ].map(opt => (
                    <label key={opt.id}
                      className={`flex gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                        scrapeMode === opt.id
                          ? 'border-primary bg-primary/5'
                          : 'border-white-light dark:border-[#1b2e4b] hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b]'
                      }`}>
                      <input type="radio" name="scrapeMode" value={opt.id}
                        checked={scrapeMode === opt.id}
                        onChange={() => setScrapeMode(opt.id)}
                        className="mt-1 accent-[#4361ee] flex-none" />
                      <span>
                        <span className="block font-semibold text-black dark:text-white text-sm">{opt.title}</span>
                        <span className="block text-xs text-[#888ea8] leading-relaxed">{opt.desc}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}

              {siteCheck?.valid && !siteCheck.exists && (
                <p className="text-xs text-[#888ea8]">
                  This is a new store — its data will be kept in its own folder, separate from
                  anything already scraped.
                </p>
              )}
            </div>

            <div className="flex justify-end gap-2 px-6 py-4 border-t border-white-light dark:border-[#1b2e4b]">
              <button onClick={() => setConfirmOpen(false)} className="btn btn-outline-secondary">Cancel</button>
              <button onClick={startScrape} disabled={!siteCheck?.valid} className="btn btn-primary gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
                <PlayCircle className="w-4 h-4" /> Start Scraping
              </button>
            </div>
          </div>
        </div>
      )}

      {/* File Preview Modal */}
      {previewFile && (() => {
        const kind = getFileKind(previewFile.name);
        const meta = FILE_KIND_META[kind];
        const PreviewIcon = meta.Icon;
        const rawUrl = `${API}/data/${previewFile.path}`;
        return (
          <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 sm:p-6" onClick={closePreview}>
            <div className="bg-white dark:bg-[#0e1726] rounded-lg shadow-xl w-full max-w-4xl max-h-full flex flex-col overflow-hidden animate-[scaleIn_0.2s_ease-out]" onClick={(e) => e.stopPropagation()}>

              <div className="flex items-center justify-between px-6 py-4 border-b border-white-light dark:border-[#1b2e4b] gap-4">
                <div className="flex items-center gap-3 min-w-0">
                  <div className={`w-9 h-9 rounded-md flex items-center justify-center flex-none ${meta.iconBox}`}>
                    <PreviewIcon className="w-4 h-4" />
                  </div>
                  <h3 className="text-lg font-bold text-black dark:text-white truncate">{previewFile.name}</h3>
                </div>
                <div className="flex items-center gap-2 flex-none">
                  <a href={rawUrl} target="_blank" rel="noreferrer" className="btn btn-outline-primary gap-2 py-1.5 px-3 text-xs">
                    <ExternalLink className="w-3.5 h-3.5" /> Open in New Tab
                  </a>
                  <button onClick={closePreview} className="p-1.5 rounded-md hover:bg-[#f4f4f4] dark:hover:bg-[#1b2e4b] text-[#888ea8]">
                    <X className="w-5 h-5" />
                  </button>
                </div>
              </div>

              <div className="overflow-y-auto p-6 flex-1">
                {kind === 'image' && (
                  <img src={rawUrl} alt={previewFile.name} className="max-h-[70vh] w-full object-contain rounded-md mx-auto" />
                )}

                {kind !== 'image' && previewLoading && (
                  <div className="py-20 flex flex-col items-center justify-center text-[#888ea8] gap-3">
                    <Loader2 className="w-8 h-8 animate-spin" />
                    Loading file…
                  </div>
                )}

                {kind !== 'image' && !previewLoading && previewError && (
                  <div className="py-20 flex flex-col items-center justify-center text-center gap-2">
                    <AlertTriangle className="w-8 h-8 text-warning" />
                    <p className="text-black dark:text-white font-semibold">Couldn't load this file</p>
                    <p className="text-[#888ea8] text-sm">{previewError}</p>
                  </div>
                )}

                {kind === 'json' && !previewLoading && !previewError && previewContent !== null && (
                  (() => {
                    let pretty = previewContent;
                    let parseError = null;
                    try { pretty = JSON.stringify(JSON.parse(previewContent), null, 2); } catch (e) { parseError = e.message; }
                    return (
                      <>
                        {parseError && (
                          <p className="text-warning text-xs mb-2">Not valid JSON ({parseError}) — showing raw contents.</p>
                        )}
                        <pre
                          className="terminal p-4 text-xs whitespace-pre-wrap break-all max-h-[65vh] overflow-auto"
                          dangerouslySetInnerHTML={{ __html: highlightJson(pretty) }}
                        />
                      </>
                    );
                  })()
                )}

                {kind === 'markdown' && !previewLoading && !previewError && previewContent !== null && (
                  <div
                    className="markdown-body text-[15px] leading-relaxed text-black dark:text-[#c5d0e6]"
                    dangerouslySetInnerHTML={{ __html: marked.parse(previewContent) }}
                  />
                )}

                {kind === 'text' && !previewLoading && !previewError && previewContent !== null && (
                  <pre className="terminal p-4 text-xs whitespace-pre-wrap break-all max-h-[65vh] overflow-auto">{previewContent}</pre>
                )}

                {kind === 'unsupported' && !previewLoading && !previewError && (
                  <div className="py-20 flex flex-col items-center justify-center text-center gap-2">
                    <FileType className="w-10 h-10 text-[#888ea8] opacity-50" />
                    <p className="text-black dark:text-white font-semibold">No inline preview for this file type</p>
                    <p className="text-[#888ea8] text-sm">Use "Open in New Tab" above to view or download it.</p>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

function sanitizeName(name) {
  return (name || '').replace(/[^a-zA-Z0-9]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '');
}

const FILE_KIND_META = {
  image: { Icon: ImageIcon, iconBox: 'bg-warning/10 text-warning' },
  json: { Icon: FileJson2, iconBox: 'bg-primary/10 text-primary' },
  markdown: { Icon: FileText, iconBox: 'bg-secondary/10 text-secondary' },
  text: { Icon: FileType, iconBox: 'bg-[#888ea8]/10 text-[#888ea8]' },
  unsupported: { Icon: FileType, iconBox: 'bg-[#888ea8]/10 text-[#888ea8]' },
};

function getFileKind(name) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (['jpg', 'jpeg', 'png', 'webp', 'gif', 'svg', 'bmp', 'avif', 'ico'].includes(ext)) return 'image';
  if (ext === 'json') return 'json';
  if (ext === 'md' || ext === 'markdown') return 'markdown';
  if (['txt', 'log', 'csv', 'xml'].includes(ext)) return 'text';
  return 'unsupported';
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function highlightJson(json) {
  const escaped = escapeHtml(json);
  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    (match) => {
      let cls = 'text-warning';
      if (match.startsWith('"')) {
        cls = match.endsWith(':') ? 'text-primary font-semibold' : 'text-success';
      } else if (match === 'true' || match === 'false') {
        cls = 'text-secondary font-semibold';
      } else if (match === 'null') {
        cls = 'text-danger font-semibold';
      }
      return `<span class="${cls}">${match}</span>`;
    }
  );
}

const EXPORT_GROUPS = [
  {
    title: 'Product data',
    items: [
      { id: 'csv', label: 'CSV', desc: 'Spreadsheet-friendly table', Icon: FileType },
      { id: 'excel', label: 'Excel', desc: 'Formatted .xlsx workbook', Icon: FileType },
      { id: 'json', label: 'JSON', desc: 'Full records, every field', Icon: FileJson2 },
      { id: 'xml', label: 'XML', desc: 'For feeds and imports', Icon: FileText },
    ],
  },
  {
    title: 'Lists',
    items: [
      { id: 'categories', label: 'Categories', desc: 'Unique category names', Icon: Folder },
      { id: 'brands', label: 'Brands', desc: 'Unique brand names', Icon: Package },
    ],
  },
  {
    title: 'File archives',
    items: [
      { id: 'archive_all', label: 'Everything', desc: 'Data files and images', Icon: Layers },
      { id: 'archive_data', label: 'Data only', desc: 'JSON and markdown, no images', Icon: FileText },
      { id: 'archive_images', label: 'Images only', desc: 'Downloaded product images', Icon: ImageIcon },
    ],
  },
];

const DISCOVERY_LABELS = {
  sitemap: 'Product sitemaps',
  woocommerce_api: 'WooCommerce Store API',
  shopify_api: 'Shopify products.json',
  wp_rest_api: 'WordPress REST API',
  listing_crawl: 'Listing-page crawl',
};

function Detail({ label, value, ok }) {
  return (
    <div className="min-w-0">
      <p className="font-bold text-[#888ea8] uppercase tracking-wider">{label}</p>
      <p className={`font-semibold truncate ${ok === false ? 'text-warning' : 'text-black dark:text-white'}`}>{value}</p>
    </div>
  );
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
