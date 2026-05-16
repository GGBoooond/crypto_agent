import { createRouter, createWebHistory } from "vue-router";
import RunDetailView from "@/views/RunDetailView.vue";
import RunListView from "@/views/RunListView.vue";
const router = createRouter({
    history: createWebHistory("/backtest/"),
    routes: [
        { path: "/", name: "runs", component: RunListView },
        { path: "/runs/:runId", name: "run-detail", component: RunDetailView, props: true },
    ],
});
export default router;
