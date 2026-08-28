const Layout = () => import("@/layout/index.vue");

export default [
  {
    path: "/operations",
    name: "Operations",
    component: Layout,
    redirect: "/operations/dashboard",
    meta: {
      icon: "ep/data-analysis",
      title: "今日行动",
      rank: 2
    },
    children: [
      {
        path: "/operations/dashboard",
        name: "MpDashboard",
        component: () => import("@/views/seiwajyuku/dashboard.vue"),
        meta: {
          title: "行动总览",
          icon: "ep/trend-charts"
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
    path: "/member-operations",
    name: "MemberOperations",
    component: Layout,
    redirect: "/operations/members",
    meta: {
      icon: "ep/user-filled",
      title: "学员",
      rank: 1
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
        path: "/operations/enrollment-applications",
        name: "EnrollmentApplications",
        component: () =>
          import("@/views/seiwajyuku/enrollment-applications.vue"),
        meta: {
          title: "待入塾申请",
          icon: "ep/document-checked",
          roles: [
            "system_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_operations",
            "ops_center_development",
            "ops_center_finance",
            "regional_manager"
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
      title: "活动与学习",
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
      },
      {
        path: "/operations/study-meetings",
        name: "StudyMeetings",
        component: () => import("@/views/seiwajyuku/study-meetings.vue"),
        meta: {
          title: "小组学习会",
          icon: "ep/notebook",
          roles: [
            "system_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_operations",
            "ops_center_learning",
            "ops_center_management",
            "ops_center_data",
            "regional_manager",
            "class_counselor",
            "group_leader",
            "volunteer_director",
            "volunteer_regional_lead",
            "volunteer_regional_service",
            "volunteer_class_counselor",
            "volunteer_group_leader",
            "read_only"
          ]
        }
      },
      {
        path: "/operations/learning-plan-review",
        name: "LearningPlanReview",
        component: () => import("@/views/seiwajyuku/learning-plan-review.vue"),
        meta: {
          title: "学习计划审核",
          icon: "ep/list-check",
          roles: [
            "system_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_operations",
            "ops_center_learning",
            "ops_center_management",
            "ops_center_data",
            "regional_manager",
            "read_only"
          ]
        }
      },
      {
        path: "/operations/learning-plan-group-meetings",
        name: "LearningPlanGroupMeetings",
        component: () =>
          import("@/views/seiwajyuku/learning-plan-group-meetings.vue"),
        meta: {
          title: "学习计划配置",
          icon: "ep/reading",
          roles: [
            "system_admin",
            "technical_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_learning",
            "ops_center_management",
            "ops_center_data"
          ]
        }
      }
    ]
  },
  {
    path: "/data-management",
    name: "DataManagement",
    component: Layout,
    redirect: "/operations/mp-entry",
    meta: {
      icon: "ep/pie-chart",
      title: "数据",
      rank: 4
    },
    children: [
      {
        path: "/operations/mp-entry",
        name: "MpEntry",
        component: () => import("@/views/seiwajyuku/mp-entry.vue"),
        meta: {
          title: "经营数据",
          icon: "ep/edit-pen",
          showParent: true,
          roles: [
            "system_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_operations",
            "ops_center_management",
            "volunteer_regional_lead"
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
      title: "管理员设置",
      rank: 5
    },
    children: [
      {
        path: "/operations/account-management",
        name: "AccountManagement",
        component: () => import("@/views/seiwajyuku/account-management.vue"),
        meta: {
          title: "账号管理",
          icon: "ep/user-filled",
          roles: ["system_admin", "technical_admin"]
        }
      },
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
      },
      {
        path: "/operations/organization-management",
        name: "OrganizationManagement",
        component: () =>
          import("@/views/seiwajyuku/organization-management.vue"),
        meta: {
          title: "班级与小组管理",
          icon: "ep/office-building",
          roles: [
            "system_admin",
            "technical_admin",
            "operations_admin",
            "ops_center_director",
            "ops_center_data"
          ]
        }
      }
    ]
  }
] satisfies RouteConfigsTable[];
