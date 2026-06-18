module.exports = {
  apps: [{
    name: 'audit-email-worker',
    script: './email_worker.js',
    cwd: __dirname,
    env: {
      NODE_ENV: 'production',
      ENV: 'production',
      EMAIL_USER: 'hafoshan@163.com',
      EMAIL_PASSWORD: 'your_163_auth_code',
      INTERNAL_API_KEY: 'audit-platform-internal-key-change-me',
      LOCAL_API: 'http://127.0.0.1:8767',
      POLL_INTERVAL_MS: '60000'
    },
    autorestart: true,
    max_memory_restart: '500M',
    restart_delay: 5000,
    max_restarts: 10
  }]
};
