import React, { Suspense, lazy, useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ConfigProvider, Spin } from 'antd';
import { useTranslation } from 'react-i18next';
import enUS from 'antd/locale/en_US';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';
import ErrorBoundary from './components/ErrorBoundary';
import AppLayout from './components/Layout';

const Dashboard = lazy(() => import('./pages/Dashboard'));
const DatasetManagement = lazy(() => import('./pages/DatasetManagement'));
const ExperimentConfig = lazy(() => import('./pages/ExperimentConfig'));
const RunResults = lazy(() => import('./pages/RunResults'));
const TraceDetail = lazy(() => import('./pages/TraceDetail'));
const VersionCompare = lazy(() => import('./pages/VersionCompare'));
const ReportView = lazy(() => import('./pages/ReportView'));
const Settings = lazy(() => import('./pages/Settings'));

const routeFallback = (
  <div style={{ minHeight: 320, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
    <Spin size="large" />
  </div>
);

const antdLocales: Record<string, typeof enUS> = { en: enUS, zh: zhCN };

const AppLocale: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { i18n } = useTranslation();
  const [locale, setLocale] = useState(() => antdLocales[i18n.language] || zhCN);

  useEffect(() => {
    const lang = i18n.language.startsWith('en') ? 'en' : 'zh';
    setLocale(antdLocales[lang] || zhCN);
    dayjs.locale(lang === 'zh' ? 'zh-cn' : 'en');
  }, [i18n.language]);

  return <ConfigProvider locale={locale} theme={{ token: { colorPrimary: '#1677ff' } }}>
    {children}
  </ConfigProvider>;
};

const App: React.FC = () => (
  <ErrorBoundary>
  <AppLocale>
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="/" element={<Suspense fallback={routeFallback}><Dashboard /></Suspense>} />
          <Route path="/datasets" element={<Suspense fallback={routeFallback}><DatasetManagement /></Suspense>} />
          <Route path="/experiment" element={<Suspense fallback={routeFallback}><ExperimentConfig /></Suspense>} />
          <Route path="/runs" element={<Suspense fallback={routeFallback}><RunResults /></Suspense>} />
          <Route path="/runs/:runId/traces" element={<Suspense fallback={routeFallback}><TraceDetail /></Suspense>} />
          <Route path="/compare" element={<Suspense fallback={routeFallback}><VersionCompare /></Suspense>} />
          <Route path="/reports" element={<Suspense fallback={routeFallback}><ReportView /></Suspense>} />
          <Route path="/settings" element={<Suspense fallback={routeFallback}><Settings /></Suspense>} />
        </Route>
      </Routes>
    </BrowserRouter>
  </AppLocale>
  </ErrorBoundary>
);

export default App;
