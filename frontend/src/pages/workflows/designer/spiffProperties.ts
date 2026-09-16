/**
 * Read/write helpers for <spiffworkflow:properties><spiffworkflow:property
 * name=".." value=".."/></spiffworkflow:properties> nested inside a task's
 * <bpmn:extensionElements> — the exact shape backend/app/workflow/parser.py
 * reads into task_spec.extensions["properties"]. Using bpmn-js's own
 * moddle types (via bpmnFactory) and modeling.updateModdleProperties keeps
 * every edit undo/redo-able, same as a built-in property. Ported from
 * PoultryPro-CBF's frontend/src/pages/workflows/designer/spiffProperties.ts.
 */
interface ModdleElement {
  $type: string
  $parent?: ModdleElement
  [key: string]: unknown
}

interface BpmnElement {
  businessObject: ModdleElement
}

interface BpmnFactory {
  create(type: string, attrs?: Record<string, unknown>): ModdleElement
}

interface Modeling {
  updateModdleProperties(
    element: BpmnElement,
    moddleElement: ModdleElement,
    properties: Record<string, unknown>
  ): void
}

const PROPERTIES_TYPE = 'spiffworkflow:Properties'
const PROPERTY_TYPE = 'spiffworkflow:Property'

function findPropertiesElement(businessObject: ModdleElement): ModdleElement | null {
  const ext = businessObject.extensionElements as ModdleElement | undefined
  if (!ext) return null
  const values = (ext.values as ModdleElement[] | undefined) || []
  return values.find((v) => v.$type === PROPERTIES_TYPE) || null
}

export function getSpiffProperty(businessObject: ModdleElement, name: string): string {
  const propsEl = findPropertiesElement(businessObject)
  if (!propsEl) return ''
  const properties = (propsEl.properties as ModdleElement[] | undefined) || []
  const prop = properties.find((p) => p.name === name)
  return prop ? (prop.value as string) || '' : ''
}

export function setSpiffProperty(
  element: BpmnElement,
  name: string,
  value: string,
  bpmnFactory: BpmnFactory,
  modeling: Modeling,
): void {
  const businessObject = element.businessObject
  let ext = businessObject.extensionElements as ModdleElement | undefined

  if (!ext) {
    ext = bpmnFactory.create('bpmn:ExtensionElements', { values: [] })
    ext!.$parent = businessObject
    modeling.updateModdleProperties(element, businessObject, { extensionElements: ext })
  }

  const found = findPropertiesElement(businessObject)
  let propsEl: ModdleElement
  if (found) {
    propsEl = found
  } else {
    if (value === '') return
    propsEl = bpmnFactory.create(PROPERTIES_TYPE, { properties: [] })
    propsEl.$parent = ext
    const newValues = [...((ext!.values as ModdleElement[] | undefined) || []), propsEl]
    modeling.updateModdleProperties(element, ext, { values: newValues })
  }

  const properties = (propsEl.properties as ModdleElement[] | undefined) || []
  const existing = properties.find((p) => p.name === name)

  if (existing) {
    if (value === '') {
      const remaining = properties.filter((p) => p !== existing)
      modeling.updateModdleProperties(element, propsEl, { properties: remaining })
    } else {
      modeling.updateModdleProperties(element, existing, { value })
    }
  } else if (value !== '') {
    const newProp = bpmnFactory.create(PROPERTY_TYPE, { name, value })
    newProp.$parent = propsEl
    modeling.updateModdleProperties(element, propsEl, { properties: [...properties, newProp] })
  }
}
