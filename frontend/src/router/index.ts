import { createRouter, createWebHistory } from 'vue-router'

import CalibrationView from '@/views/CalibrationView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/unit-calibration', name: 'calibration-workbench', component: CalibrationView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
