// React error boundary (全链路日志方案 v2.0 §6.3). Catches render-time crashes,
// reports them to the log centre (react.boundary), and shows the 500 scene with
// the trace_id so the user can quote it.
import React from 'react';
import { logger } from '../lib/logger';
import { Page500 } from '../pages/errors';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    logger.error('react.boundary', error, { component_stack: info?.componentStack });
  }

  render() {
    if (this.state.hasError) return <Page500 boundary />;
    return this.props.children;
  }
}
