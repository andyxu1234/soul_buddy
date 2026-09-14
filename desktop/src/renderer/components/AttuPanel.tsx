import { Icon } from './Icon'

const ATTU_URL = 'http://192.168.1.9:8000'
const MILVUS_HOST = '192.168.1.9'
const MILVUS_GRPC_PORT = 19530
const MILVUS_HTTP_PORT = 9091

/** Milvus Attu 入口面板 — 提供连接信息和浏览器跳转。 */
export function AttuPanel() {
  return (
    <div className="plugin-panel">
      <div className="plugin-topbar">
        <div className="plugin-title">
          <div className="plugin-title-icon" style={{ background: 'var(--brand-soft)', color: 'var(--brand-fg)' }}>
            <Icon name="database" size={16} />
          </div>
          <div>
            <h2>Milvus Attu</h2>
            <p>向量库管理界面 · 浏览器打开 {ATTU_URL}</p>
          </div>
        </div>
        <div className="attu-topbar-actions">
          <a
            className="btn-primary attu-open-btn"
            href={ATTU_URL}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Icon name="external-link" size={14} />
            在浏览器中打开
          </a>
        </div>
      </div>

      <div className="plugin-body attu-info-body">
        <div className="attu-info-card">
          <h3>连接信息</h3>
          <div className="attu-info-grid">
            <div className="attu-info-row">
              <span className="attu-info-label">Attu UI</span>
              <code>{ATTU_URL}</code>
            </div>
            <div className="attu-info-row">
              <span className="attu-info-label">Milvus gRPC</span>
              <code>{MILVUS_HOST}:{MILVUS_GRPC_PORT}</code>
            </div>
            <div className="attu-info-row">
              <span className="attu-info-label">Milvus HTTP</span>
              <code>{MILVUS_HOST}:{MILVUS_HTTP_PORT}</code>
            </div>
          </div>
        </div>

        <div className="attu-info-card">
          <h3>快速开始</h3>
          <ol className="attu-steps">
            <li>点击右上角"在浏览器中打开"按钮</li>
            <li>在 Attu 登录页填写 Milvus 地址</li>
            <li>默认数据库 <code>default</code>，留空即可连接</li>
          </ol>
        </div>

        <div className="attu-info-card attu-tip">
          <Icon name="info" size={16} />
          <span>Attu 使用 gRPC-web (WebSocket) 直连 Milvus 19530 端口，需要浏览器能访问该地址。</span>
        </div>
      </div>
    </div>
  )
}
