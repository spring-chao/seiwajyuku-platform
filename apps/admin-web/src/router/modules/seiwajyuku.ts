const Layout = () => import("@/layout/index.vue");

export default {
  path: "/operations",
  name: "Operations",
  component: Layout,
  redirect: "/operations/dashboard",
  meta: {
    icon: "ep/data-analysis",
    title: "运营驾驶舱",
    rank: 1
  },
  children: [
    {
      path: "/operations/dashboard",
      name: "MpDashboard",
      component: () => import("@/views/seiwajyuku/dashboard.vue"),
      meta: {
        title: "年度MP看板",
        icon: "ep/trend-charts"
      }
    },
    {
      path: "/operations/mp-entry",
      name: "MpEntry",
      component: () => import("@/views/seiwajyuku/mp-entry.vue"),
      meta: {
        title: "月度填报",
        icon: "ep/edit-pen",
        roles: ["system_admin", "operations_admin", "regional_manager"]
      }
    },
    {
      path: "/operations/members",
      name: "MemberManagement",
      component: () => import("@/views/seiwajyuku/members.vue"),
      meta: {
        title: "学员管理",
        icon: "ep/user",
        roles: [
          "system_admin",
          "operations_admin",
          "regional_manager",
          "class_counselor",
          "group_leader",
          "read_only"
        ]
      }
    },
    {
      path: "/operations/followups",
      name: "FollowupTasks",
      component: () => import("@/views/seiwajyuku/followups.vue"),
      meta: {
        title: "关怀跟进",
        icon: "ep/phone",
        roles: [
          "system_admin",
          "operations_admin",
          "regional_manager",
          "class_counselor",
          "group_leader"
        ]
      }
    },
    {
      path: "/operations/renewals",
      name: "RenewalOperations",
      component: () => import("@/views/seiwajyuku/renewals.vue"),
      meta: {
        title: "续费运营",
        icon: "ep/refresh-right",
        roles: [
          "system_admin",
          "operations_admin",
          "regional_manager",
          "class_counselor",
          "group_leader",
          "read_only"
        ]
      }
    },
    {
      path: "/operations/activities",
      name: "ActivityAdmin",
      component: () => import("@/views/seiwajyuku/activities.vue"),
      meta: {
        title: "活动与签到",
        icon: "ep/calendar",
        roles: [
          "system_admin",
          "operations_admin",
          "regional_manager",
          "class_counselor",
          "group_leader",
          "read_only"
        ]
      }
    }
  ]
} satisfies RouteConfigsTable;
