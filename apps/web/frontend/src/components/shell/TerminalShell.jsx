// The page frame: module strip · sidebar · sub-toolbar · title · filters · body · status bar.
import React from 'react';
import { ModuleStrip, PageTitleBar, StatusBar, SubToolbar, TerminalSidebar } from './parts';

export function TerminalShell({
  module = 'data', current,
  path = [], fkeys = [], subToolbarRight,
  title, subtitle, badge, actions, titleBar = true,
  filterBar, children, statusMiddle, statusRight,
}) {
  return (
    <div style={{
      width: '100%', height: '100%', background: 'var(--bg)', color: 'var(--fg)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden', fontSize: 12,
    }}>
      <ModuleStrip current={module === 'viz' ? 'viz' : module} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <TerminalSidebar module={module} current={current} />
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <SubToolbar path={path} fkeys={fkeys} right={subToolbarRight} />
          {titleBar && title && <PageTitleBar title={title} subtitle={subtitle} badge={badge} actions={actions} />}
          {filterBar}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
            {children}
          </div>
          <StatusBar middle={statusMiddle} right={statusRight} />
        </main>
      </div>
    </div>
  );
}
