import { createRouter, createWebHistory } from 'vue-router'

import AttributionReceiptView from '@/views/AttributionReceiptView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/attribution_receipt', name: 'attribution-receipt', component: AttributionReceiptView },
    { path: '/gateway_mgmt/receipt', name: 'gateway-receipt', component: AttributionReceiptView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
