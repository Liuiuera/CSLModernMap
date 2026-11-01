import { GraphAttributes, NodeAttributes } from '../constants/constants';

export const stationNameVisible = (
    node: Pick<NodeAttributes, 'cmmName'>,
    settings?: GraphAttributes['cmmNameDisplay']
): boolean => {
    if (settings?.showNames === false) return false;
    if (node.cmmName?.visibility === 'hide') return false;
    if (node.cmmName?.visibility === 'show') return true;
    return (
        settings?.showDefaultNames !== false ||
        node.cmmName?.edited === true ||
        !['game', 'placeholder'].includes(node.cmmName?.source ?? 'unknown')
    );
};

export const validateNameMetadata = (node: NodeAttributes): void => {
    const value = node.cmmName;
    if (value === undefined) return;
    if (
        !value ||
        !['custom', 'game', 'placeholder', 'unknown'].includes(value.source) ||
        (value.edited !== undefined && typeof value.edited !== 'boolean') ||
        (value.visibility !== undefined && !['inherit', 'show', 'hide'].includes(value.visibility)) ||
        (value.importedSource !== undefined && !['custom', 'game', 'unknown'].includes(value.importedSource)) ||
        (value.importedNames !== undefined &&
            (!Array.isArray(value.importedNames) ||
                value.importedNames.length === 0 ||
                value.importedNames.some(name => typeof name !== 'string')))
    )
        throw new Error('Invalid station name metadata');
};

export const validateNameDisplay = (value: GraphAttributes['cmmNameDisplay']): void => {
    if (
        value !== undefined &&
        (!value || typeof value.showNames !== 'boolean' || typeof value.showDefaultNames !== 'boolean')
    )
        throw new Error('Invalid station name display settings');
};
