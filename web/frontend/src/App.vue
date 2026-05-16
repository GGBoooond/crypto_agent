<template>
  <div class="mx-auto min-h-screen max-w-7xl px-6 py-6">
    <header class="mb-6 flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-slate-100">Backtest Center</h1>
        <p class="text-sm text-slate-400">可视化启动回测、跟踪进度并查看回测报告</p>
      </div>
      <a href="/" class="rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800">
        返回实时监控
      </a>
    </header>
    <RouterView />
  </div>
</template>
