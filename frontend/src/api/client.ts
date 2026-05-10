import axios from 'axios';
import { message } from 'antd';
import { getApiBaseUrl } from './base';
import i18n from '../i18n';

const api = axios.create({
  baseURL: getApiBaseUrl(),
  timeout: 30000,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const { status } = error.response;
      if (status === 500) {
        message.error(i18n.t('api.serverError'));
      } else if (status === 401 || status === 403) {
        message.error(i18n.t('api.authError'));
      }
    } else if (error.request) {
      message.error(i18n.t('api.networkError'));
    }
    return Promise.reject(error);
  }
);

// Datasets
export const datasetApi = {
  list: (params?: { case_type?: string; skip?: number; limit?: number }) =>
    api.get('/datasets', { params }),
  get: (id: number) => api.get(`/datasets/${id}`),
  create: (data: { name: string; description?: string; case_type?: string }) =>
    api.post('/datasets', data),
  update: (id: number, data: Record<string, unknown>) => api.put(`/datasets/${id}`, data),
  delete: (id: number) => api.delete(`/datasets/${id}`),
  listCases: (datasetId: number, params?: Record<string, unknown>) =>
    api.get(`/datasets/${datasetId}/cases`, { params }),
  createCase: (datasetId: number, data: Record<string, unknown>) =>
    api.post(`/datasets/${datasetId}/cases`, data),
  importCases: (datasetId: number, cases: Record<string, unknown>[]) =>
    api.post(`/datasets/${datasetId}/import`, { cases }),
};

// Versions
export const versionApi = {
  list: (params?: { version_type?: string }) => api.get('/versions', { params }),
  create: (data: {
    version_type: string; name: string; config_json: Record<string, unknown>;
    description?: string;
  }) => api.post('/versions', data),
  delete: (id: number) => api.delete(`/versions/${id}`),
};

// Runs
export const runApi = {
  list: (params?: Record<string, unknown>) => api.get('/runs', { params }),
  get: (id: number) => api.get(`/runs/${id}`),
  create: (data: {
    name: string; dataset_id: number; provider?: string; model?: string; model_version_id?: number;
    prompt_version_id?: number; retriever_version_id?: number;
    agent_version_id?: number; concurrency?: number;
    target_url?: string; target_type?: string; target_headers?: Record<string, string>;
    target_timeout?: number;
  }) => api.post('/runs', data),
  progress: (id: number) => api.get(`/runs/${id}/progress`),
  results: (id: number, params?: Record<string, unknown>) =>
    api.get(`/runs/${id}/results`, { params }),
  compare: (run1: number, run2: number) =>
    api.get('/runs/compare', { params: { run1, run2 } }),
};

// Config
export const configApi = {
  providers: () => api.get('/config/providers'),
  providerModels: (provider: string) => api.get(`/config/providers/${provider}/models`),
  apiKeys: () => api.get('/config/apikeys'),
  setApiKey: (data: {
    provider: string; api_key: string; base_url?: string; default_model?: string;
  }) => api.post('/config/apikeys', data),
  deleteApiKey: (provider: string) => api.delete(`/config/apikeys/${provider}`),
};

// Dashboard
export const dashboardApi = {
  stats: () => api.get('/dashboard/stats'),
  health: () => api.get('/dashboard/health'),
};

// Reports
export const reportApi = {
  generate: (runId: number) => api.post(`/runs/${runId}/report`),
  get: (id: number) => api.get(`/reports/${id}`),
  list: (params?: { run_id?: number; skip?: number; limit?: number }) =>
    api.get('/reports', { params }),
  byRun: (runId: number) => api.get(`/runs/${runId}/reports`),
};

// Traces
export const traceApi = {
  list: (params?: { run_id?: number; status?: string; skip?: number; limit?: number }) =>
    api.get('/traces', { params }),
  get: (traceId: string) => api.get(`/traces/${traceId}`),
  byRun: (runId: number, params?: { skip?: number; limit?: number }) =>
    api.get(`/runs/${runId}/traces`, { params }),
};
