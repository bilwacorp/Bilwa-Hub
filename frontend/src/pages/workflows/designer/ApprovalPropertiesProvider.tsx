/**
 * Custom properties-panel group exposing the approval-engine fields on a
 * BPMN user/manual task: stepKey (required — see backend's
 * app/workflow/parser.py's validate_bpmn, which rejects a task with none),
 * ruleKey, casbinResource, casbinAction, dueInHours. Values live in
 * <spiffworkflow:properties> — see spiffProperties.ts.
 *
 * Ported from PoultryPro-CBF's ApprovalPropertiesProvider.tsx. Mirrors the
 * officially documented custom-provider pattern
 * (bpmn-io/bpmn-js-examples/properties-panel-extension).
 *
 * No JSX anywhere in this file, deliberately: bpmn-js-properties-panel
 * renders its tree with Preact (bundled transitively via
 * @bpmn-io/diagram-js-ui), not React. This file lives inside a
 * React-JSX-pragma project, so `<Foo/>` here would compile to a *React*
 * element — which Preact's reconciler doesn't understand and fails deep
 * inside its internals. Calling these Preact function components directly
 * as plain functions sidesteps the JSX pragma entirely and returns a real
 * Preact vnode tree.
 *
 * Never goes through React's render tree, so react-refresh's
 * only-export-components rule doesn't apply — disabled file-wide.
 */
/* eslint-disable react-refresh/only-export-components */
import { is } from 'bpmn-js/lib/util/ModelUtil'
import { TextFieldEntry, isTextFieldEntryEdited } from '@bpmn-io/properties-panel'
import { useService } from 'bpmn-js-properties-panel'
import { getSpiffProperty, setSpiffProperty } from './spiffProperties'

interface PropertyField {
  key: string
  label: string
  description?: string
}

const PROPERTY_FIELDS: PropertyField[] = [
  { key: 'stepKey', label: 'Step key', description: 'Identifies this step to the rule engine — required on every user/manual task.' },
  { key: 'ruleKey', label: 'Rule key (optional)', description: 'Pins this step to one specific rule by key instead of normal priority lookup.' },
  { key: 'casbinResource', label: 'Casbin resource (optional)', description: 'Extra permission check an approver must hold, alongside being a candidate.' },
  { key: 'casbinAction', label: 'Casbin action (optional)' },
  { key: 'dueInHours', label: 'Due in hours (optional)' },
]

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ApprovalTextField({ element, field }: { element: any; field: PropertyField }) {
  const modeling = useService('modeling')
  const bpmnFactory = useService('bpmnFactory')
  const debounce = useService('debounceInput')
  const translate = useService('translate')

  const getValue = () => getSpiffProperty(element.businessObject, field.key)
  const setValue = (value: string) => setSpiffProperty(element, field.key, value, bpmnFactory, modeling)

  return TextFieldEntry({
    id: field.key,
    element,
    label: translate(field.label),
    description: field.description ? translate(field.description) : undefined,
    getValue,
    setValue,
    debounce,
  })
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function approvalGroup(element: any) {
  if (!is(element, 'bpmn:UserTask') && !is(element, 'bpmn:ManualTask')) return null
  return {
    id: 'approvalProperties',
    label: 'Approval routing',
    entries: PROPERTY_FIELDS.map((field) => ({
      id: field.key,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      component: (props: any) => ApprovalTextField({ ...props, field }),
      isEdited: isTextFieldEntryEdited,
    })),
  }
}

export default class ApprovalPropertiesProvider {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  getGroups: (element: any) => (groups: any[]) => any[]

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  constructor(propertiesPanel: any) {
    this.getGroups = (element) => (groups) => {
      const group = approvalGroup(element)
      if (group) groups.push(group)
      return groups
    }
    propertiesPanel.registerProvider(500, this)
  }

  static $inject = ['propertiesPanel']
}
