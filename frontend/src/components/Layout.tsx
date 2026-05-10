import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout as AntLayout, Menu, Typography, Select, Space } from 'antd';
import { useTranslation } from 'react-i18next';
import {
  DashboardOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  BarChartOutlined,
  SettingOutlined,
  SwapOutlined,
  FileTextOutlined,
} from '@ant-design/icons';

const { Header, Sider, Content } = AntLayout;
const { Title } = Typography;

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { t, i18n } = useTranslation();

  const lang = i18n.language.startsWith('zh') ? 'zh' : 'en';

  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: t('layout.menu.dashboard') },
    { key: '/datasets', icon: <DatabaseOutlined />, label: t('layout.menu.datasets') },
    { key: '/experiment', icon: <ExperimentOutlined />, label: t('layout.menu.experiment') },
    { key: '/runs', icon: <BarChartOutlined />, label: t('layout.menu.runs') },
    { key: '/compare', icon: <SwapOutlined />, label: t('layout.menu.compare') },
    { key: '/reports', icon: <FileTextOutlined />, label: t('layout.menu.reports') },
    { key: '/settings', icon: <SettingOutlined />, label: t('layout.menu.settings') },
  ];

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Sider width={220} theme="dark">
        <div style={{ padding: '16px', textAlign: 'center' }}>
          <Title level={5} style={{ color: '#fff', margin: 0 }}>
            {t('layout.title')}
          </Title>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <AntLayout>
        <Header
          style={{
            background: '#fff',
            padding: '0 24px',
            borderBottom: '1px solid #f0f0f0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <Title level={4} style={{ margin: 0 }}>
            {t('layout.subtitle')}
          </Title>
          <Space>
            <Select
              value={lang}
              onChange={(value) => i18n.changeLanguage(value)}
              style={{ width: 110 }}
              options={[
                { value: 'zh', label: '中文' },
                { value: 'en', label: 'English' },
              ]}
            />
          </Space>
        </Header>
        <Content style={{ margin: 16, padding: 24, background: '#fff', borderRadius: 8 }}>
          <Outlet />
        </Content>
      </AntLayout>
    </AntLayout>
  );
};

export default AppLayout;
