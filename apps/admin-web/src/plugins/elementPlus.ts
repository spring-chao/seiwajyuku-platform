// 按需引入element-plus（该方法稳定且明确。当然也支持：https://element-plus.org/zh-CN/guide/quickstart.html#%E6%8C%89%E9%9C%80%E5%AF%BC%E5%85%A5）
import type { App, Component } from "vue";
import {
  /**
   * 为了方便演示平台将 element-plus 导出的所有组件引入，实际使用中如果你没用到哪个组件，将其注释掉就行
   * 导出来源：https://github.com/element-plus/element-plus/blob/dev/packages/element-plus/component.ts#L111-L211
   * */
  ElAlert,
  ElAutocomplete,
  ElAvatar,
  ElBacktop,
  ElBadge,
  ElBreadcrumb,
  ElBreadcrumbItem,
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElCol,
  ElCollapse,
  ElCollapseItem,
  ElConfigProvider,
  ElDatePicker,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDivider,
  ElDrawer,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElIcon,
  ElImage,
  ElInput,
  ElInputNumber,
  ElLink,
  ElMenu,
  ElMenuItem,
  ElSubMenu,
  ElPagination,
  ElPopconfirm,
  ElPopover,
  ElPopper,
  ElProgress,
  ElRadio,
  ElRadioButton,
  ElRadioGroup,
  ElResult,
  ElScrollbar,
  ElSelect,
  ElOption,
  ElOptionGroup,
  ElSkeleton,
  ElSpace,
  ElStatistic,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTabPane,
  ElTag,
  ElText,
  ElTooltip,
  ElTreeSelect,
  ElUpload,
  /**
   * 为了方便演示平台将 element-plus 导出的所有插件引入，实际使用中如果你没用到哪个插件，将其注释掉就行
   * 导出来源：https://github.com/element-plus/element-plus/blob/dev/packages/element-plus/plugin.ts#L11-L16
   * */
  ElLoading, // v-loading 指令
  ElInfiniteScroll, // v-infinite-scroll 指令
  ElPopoverDirective, // v-popover 指令
  ElMessage, // $message 全局属性对象globalProperties
  ElMessageBox, // $msgbox、$alert、$confirm、$prompt 全局属性对象globalProperties
  ElNotification // $notify 全局属性对象globalProperties
} from "element-plus";

const components = [
  ElAlert,
  ElAutocomplete,
  ElAvatar,
  ElBacktop,
  ElBadge,
  ElBreadcrumb,
  ElBreadcrumbItem,
  ElButton,
  ElCard,
  ElCheckbox,
  ElCheckboxGroup,
  ElCol,
  ElCollapse,
  ElCollapseItem,
  ElConfigProvider,
  ElDatePicker,
  ElDescriptions,
  ElDescriptionsItem,
  ElDialog,
  ElDivider,
  ElDrawer,
  ElDropdown,
  ElDropdownItem,
  ElDropdownMenu,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElIcon,
  ElImage,
  ElInput,
  ElInputNumber,
  ElLink,
  ElMenu,
  ElMenuItem,
  ElSubMenu,
  ElPagination,
  ElPopconfirm,
  ElPopover,
  ElPopper,
  ElProgress,
  ElRadio,
  ElRadioButton,
  ElRadioGroup,
  ElResult,
  ElScrollbar,
  ElSelect,
  ElOption,
  ElOptionGroup,
  ElSkeleton,
  ElSpace,
  ElStatistic,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTabs,
  ElTabPane,
  ElTag,
  ElText,
  ElTooltip,
  ElTreeSelect,
  ElUpload,
];

const plugins = [
  ElLoading,
  ElInfiniteScroll,
  ElPopoverDirective,
  ElMessage,
  ElMessageBox,
  ElNotification
];

/** 按需引入`element-plus` */
export function useElementPlus(app: App) {
  // 全局注册组件
  components.forEach((component: Component) => {
    app.component(component.name, component);
  });
  // 全局注册插件
  plugins.forEach(plugin => {
    app.use(plugin);
  });
}
