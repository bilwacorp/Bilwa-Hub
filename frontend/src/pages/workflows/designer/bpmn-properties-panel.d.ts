/**
 * Neither package ships TypeScript declarations. Both are only ever used
 * from ApprovalPropertiesProvider.tsx and BpmnDesigner.tsx, where every
 * value crossing this boundary is already re-typed locally — so `any`
 * here doesn't leak imprecision into the rest of the app, it just stops
 * at the door.
 */
declare module '@bpmn-io/properties-panel' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const TextFieldEntry: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const isTextFieldEntryEdited: any
  export { TextFieldEntry, isTextFieldEntryEdited }
}

declare module 'bpmn-js-properties-panel' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const BpmnPropertiesPanelModule: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const BpmnPropertiesProviderModule: any
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const useService: (name: string) => any
  export { BpmnPropertiesPanelModule, BpmnPropertiesProviderModule, useService }
}
