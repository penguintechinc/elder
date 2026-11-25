/**
 * Express.js server with license integration
 */

import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import compression from 'compression';
import morgan from 'morgan';
import dotenv from 'dotenv';
import { createPrometheusMetrics } from './lib/metrics.js';
import { initializeLicensing, getClient } from './lib/license-client.js';

// Load environment variables
dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// Initialize Prometheus metrics
const metrics = createPrometheusMetrics();

// Middleware
app.use(helmet());
app.use(cors());
app.use(compression());
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));

// Logging middleware
app.use(morgan('combined'));

// Metrics middleware
app.use((req, res, next) => {
  const start = Date.now();

  res.on('finish', () => {
    const duration = (Date.now() - start) / 1000;
    metrics.httpRequestDuration
      .labels(req.method, req.route?.path || req.path, res.statusCode)
      .observe(duration);

    metrics.httpRequestsTotal
      .labels(req.method, req.route?.path || req.path, res.statusCode)
      .inc();
  });

  next();
});

// Initialize licensing
let licenseInfo = null;
try {
  licenseInfo = await initializeLicensing();
  console.log('License validation successful');
} catch (error) {
  console.error('License validation failed:', error.message);
  // Continue without license - some features will be unavailable
}

// License middleware
app.use((req, res, next) => {
  req.licenseClient = getClient();
  req.licenseInfo = licenseInfo;
  next();
});

// Feature gate middleware factory
function requireFeature(featureName) {
  return async (req, res, next) => {
    if (!req.licenseClient) {
      return res.status(500).json({
        error: 'license_client_unavailable',
        message: 'License client not initialized'
      });
    }

    try {
      const hasFeature = await req.licenseClient.checkFeature(featureName);
      if (!hasFeature) {
        return res.status(403).json({
          error: 'feature_not_available',
          message: `Feature '${featureName}' requires license upgrade`,
          feature: featureName
        });
      }
      next();
    } catch (error) {
      console.error(`Feature check failed for ${featureName}:`, error);
      return res.status(500).json({
        error: 'feature_check_failed',
        message: 'Unable to verify feature availability'
      });
    }
  };
}

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({
    status: 'healthy',
    timestamp: new Date().toISOString(),
    version: process.env.VERSION || 'development',
    license: licenseInfo ? 'valid' : 'invalid'
  });
});

// Metrics endpoint
app.get('/metrics', async (req, res) => {
  res.set('Content-Type', metrics.register.contentType);
  const metricsOutput = await metrics.register.metrics();
  res.send(metricsOutput);
});

// API routes
const apiRouter = express.Router();

// License information endpoint
apiRouter.get('/license', (req, res) => {
  if (!req.licenseInfo) {
    return res.status(500).json({
      error: 'license_validation_failed',
      message: 'License validation failed'
    });
  }

  res.json({
    customer: req.licenseInfo.customer,
    tier: req.licenseInfo.tier,
    features: req.licenseInfo.features?.filter(f => f.entitled) || [],
    expires_at: req.licenseInfo.expires_at
  });
});

// Features endpoint
apiRouter.get('/features', async (req, res) => {
  if (!req.licenseClient) {
    return res.status(500).json({
      error: 'license_client_unavailable',
      message: 'License client not available'
    });
  }

  try {
    const features = await req.licenseClient.getAllFeatures();
    res.json({ features });
  } catch (error) {
    console.error('Failed to get features:', error);
    res.status(500).json({
      error: 'feature_fetch_failed',
      message: 'Unable to fetch available features'
    });
  }
});

// Basic API endpoints
apiRouter.get('/status', (req, res) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    version: process.env.VERSION || 'development'
  });
});

// Feature-gated endpoints
apiRouter.get('/analytics', requireFeature('advanced_analytics'), (req, res) => {
  // Track feature usage
  if (req.licenseClient) {
    req.licenseClient.keepalive({
      feature_usage: {
        advanced_analytics: { last_used: new Date().toISOString() }
      }
    }).catch(error => {
      console.error('Failed to send keepalive:', error);
    });
  }

  res.json({
    message: 'Advanced analytics data',
    data: {
      metrics: ['user_engagement', 'performance_stats', 'conversion_rates'],
      timeframe: '30_days',
      generated_at: new Date().toISOString()
    }
  });
});

apiRouter.get('/enterprise', requireFeature('enterprise_features'), (req, res) => {
  // Track feature usage
  if (req.licenseClient) {
    req.licenseClient.keepalive({
      feature_usage: {
        enterprise_features: { last_used: new Date().toISOString() }
      }
    }).catch(error => {
      console.error('Failed to send keepalive:', error);
    });
  }

  res.json({
    message: 'Enterprise features',
    features: {
      audit_logs: 'enabled',
      advanced_security: 'enabled',
      priority_support: 'available',
      custom_integrations: 'enabled'
    }
  });
});

// User management (enterprise feature)
apiRouter.get('/users', requireFeature('user_management'), (req, res) => {
  res.json({
    users: [
      { id: 1, name: 'Admin User', role: 'admin', active: true },
      { id: 2, name: 'Regular User', role: 'user', active: true }
    ],
    total: 2,
    active: 2
  });
});

// Reports endpoint (enterprise feature)
apiRouter.get('/reports', requireFeature('enterprise_reports'), (req, res) => {
  res.json({
    reports: [
      {
        id: 'security_audit',
        name: 'Security Audit Report',
        last_generated: new Date().toISOString(),
        status: 'completed'
      },
      {
        id: 'compliance',
        name: 'Compliance Report',
        last_generated: new Date().toISOString(),
        status: 'completed'
      },
      {
        id: 'usage_analytics',
        name: 'Usage Analytics Report',
        last_generated: new Date().toISOString(),
        status: 'completed'
      }
    ]
  });
});

// Mount API router
app.use('/api/v1', apiRouter);

// Serve static files in production
if (process.env.NODE_ENV === 'production') {
  app.use(express.static('dist'));

  app.get('*', (req, res) => {
    res.sendFile(path.join(process.cwd(), 'dist', 'index.html'));
  });
}

// Error handling middleware
app.use((error, req, res, next) => {
  console.error('Server error:', error);

  res.status(error.status || 500).json({
    error: error.name || 'internal_server_error',
    message: error.message || 'An internal server error occurred'
  });
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({
    error: 'not_found',
    message: 'The requested resource was not found'
  });
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('SIGTERM received, shutting down gracefully');
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('SIGINT received, shutting down gracefully');
  process.exit(0);
});

// Start server
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
  console.log(`Health check: http://localhost:${PORT}/health`);
  console.log(`Metrics: http://localhost:${PORT}/metrics`);

  if (licenseInfo) {
    console.log(`License: ${licenseInfo.customer} (${licenseInfo.tier} tier)`);
  } else {
    console.log('Warning: Running without valid license - some features unavailable');
  }
});

export default app;
