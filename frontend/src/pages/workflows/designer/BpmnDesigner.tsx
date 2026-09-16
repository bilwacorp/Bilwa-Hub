/**
 * Thin wrapper around bpmn-js's full Modeler (canvas + properties panel).
 * Always lazy-loaded from the page that uses it (see WorkflowDesignerPage)
 * — bpmn-modeler + the properties panel's CodeMirror dependency add
 * roughly 200KB gzipped, only worth paying for on the one page that edits
 * diagrams. Ported from PoultryPro-CBF's BpmnDesigner.tsx.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import BpmnModeler from 'bpmn-js/lib/Modeler'
import { BpmnPropertiesPanelModule, BpmnPropertiesProviderModule } from 'bpmn-js-properties-panel'
import approvalPropertiesProviderModule from './approvalPropertiesProviderModule'
import spiffworkflowModdle from './spiffworkflowModdle.json'

import 'bpmn-js/dist/assets/diagram-js.css'
import 'bpmn-js/dist/assets/bpmn-js.css'
import 'bpmn-js/dist/assets/bpmn-font/css/bpmn-embedded.css'
import '@bpmn-io/properties-panel/assets/properties-panel.css'

export interface BpmnDesignerHandle {
  exportXML: () => Promise<string>
  importXML: (xml: string) => Promise<void>
}

interface BpmnDesignerProps {
  initialXml: string
  onChanged?: () => void
  onImportError?: (message: string) => void
}

export const BpmnDesigner = forwardRef<BpmnDesignerHandle, BpmnDesignerProps>(
  ({ initialXml, onChanged, onImportError }, ref) => {
    const canvasRef = useRef<HTMLDivElement>(null)
    const panelRef = useRef<HTMLDivElement>(null)
    // bpmn-js ships no first-party TS types — the modeler instance is
    // treated as an opaque handle here, only ever passed back into its own
    // API (importXML/saveXML/on/destroy).
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const modelerRef = useRef<any>(null)

    useEffect(() => {
      if (!canvasRef.current || !panelRef.current) return

      // StrictMode mounts, cleans up, and remounts every effect once in
      // dev — the first modeler's in-flight importXML() rejects once
      // destroy() below runs. `live` swallows that artifact so it never
      // surfaces as a spurious error toast; the real (second) mount's
      // import still reports genuine failures normally.
      let live = true

      const modeler = new BpmnModeler({
        container: canvasRef.current,
        propertiesPanel: { parent: panelRef.current },
        additionalModules: [BpmnPropertiesPanelModule, BpmnPropertiesProviderModule, approvalPropertiesProviderModule],
        moddleExtensions: { spiffworkflow: spiffworkflowModdle },
      })
      modelerRef.current = modeler

      modeler.importXML(initialXml).catch((err: Error) => {
        if (live) onImportError?.(err.message)
      })

      if (onChanged) {
        modeler.on('commandStack.changed', onChanged)
      }

      return () => {
        live = false
        modeler.destroy()
        modelerRef.current = null
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    useImperativeHandle(ref, () => ({
      exportXML: async () => {
        if (!modelerRef.current) throw new Error('Designer is not ready yet')
        const { xml } = await modelerRef.current.saveXML({ format: true })
        return xml as string
      },
      importXML: async (xml: string) => {
        if (!modelerRef.current) throw new Error('Designer is not ready yet')
        await modelerRef.current.importXML(xml)
      },
    }))

    return (
      <div className="flex h-full min-h-[520px] border border-border rounded-lg overflow-hidden">
        <div ref={canvasRef} className="flex-1 bg-white relative" />
        <div ref={panelRef} className="w-[320px] border-l border-border overflow-y-auto bg-surface shrink-0 text-sm" />
      </div>
    )
  }
)
BpmnDesigner.displayName = 'BpmnDesigner'
