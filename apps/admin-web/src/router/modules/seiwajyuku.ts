const Layout = () => import("@/layout/index.vue");

export default [
  {
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
        title: "运营总览",
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
        roles: [
          "system_admin",
          "operations_admin",
          "ops_center_director",
          "ops_center_operations",
          "ops_center_management",
          "volunteer_regional_lead"
        ]
      }
    },
    ]
  },
  {
    path: "/member-operations",
    name: "MemberOperations",
    component: Layout,
    redirect: "/operations/members",
    meta: {
      icon: "ep/user-filled",
      title: "学员运营",
      rank: 2
    },
    children: [
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
            "ops_center_director",
            "ops_center_operations",
            "ops_center_learning",
            "ops_center_development",
            "ops_center_management",
            "ops_center_data",
            "ops_center_administration",
            "regional_manager",
            "class_counselor",
            "group_leader",
            "volunteer_director",
            "volunteer_regional_lead",
            "volunteer_regional_service",
            "volunteer_class_counselor",
            "volunteer_class_committee",
            "volunteer_group_leader",
            "volunteer_group_committee",
            "volunteer_activity",
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
            "ops_center_director",
            "ops_center_operations",
            "ops_center_learning",
            "ops_center_development",
            "ops_center_administration",
            "regional_manager",
            "class_counselor",
            "group_leader",
            "volunteer_director",
            "volunteer_regional_lead",
            "volunteer_regional_service",
            "volunteer_class_counselor",
            "volunteer_class_committee",
            "volunteer_group_leader",
            "volunteer_group_committee"
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
            "ops_center_director",
            "ops_center_operations",
            "ops_center_development",
            "ops_center_data",
            "ops_center_finance",
            "regional_manager",
            "class_counselor",
            "group_leader",
            "volunteer_director",
            "volunteer_regional_lead",
            "volunteer_regional_service",
            "volunteer_class_counselor",
            "volunteer_class_committee",
            "volunteer_group_leader",
            "read_only"
          ]
        }
      }
    ]
  },
  {
    path: "/activity-management",
    name: "ActivityManagement",
    component: Layout,
    redirect: "/operations/activities",
    meta: {
      icon: "ep/calendar",
      title: "活动管理",
      rank: 3
    },
    children: [
      {
        path: "/operations/activities",
        name: "ActivityAdmin",
        component: () => import("@/views/seiwajyuku/activities.vue"),
        meta: {
          title: "活动与签到",
          icon: "ep/calendar",
          showParent: true,
          roles: [
            "system_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_learning",
            "ops_center_data",
            "regional_manager",
            "class_counselor",
            "group_leader",
            "volunteer_director",
            "volunteer_regional_lead",
            "volunteer_regional_service",
            "volunteer_class_counselor",
            "volunteer_class_committee",
            "volunteer_group_leader",
            "volunteer_group_committee",
            "volunteer_activity",
            "read_only"
          ]
        }
      }
    ]
  },
  {
    path: "/system-settings",
    name: "SystemSettings",
    component: Layout,
    redirect: "/operations/identity-admin",
    meta: {
      icon: "ep/setting",
      title: "系统设置",
      rank: 4
    },
    children: [
      {
        path: "/operations/identity-admin",
        name: "IdentityAdmin",
        component: () => import("@/views/seiwajyuku/identity-admin.vue"),
        meta: {
          title: "身份与任职",
          icon: "ep/key",
          showParent: true,
          roles: ["system_admin", "technical_admin"]
        }
      }
    ]
  }
] satisfies RouteConfigsTable[];
