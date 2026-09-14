import * as origin from './urlFactory.origin';
import * as edge from './urlFactory.edge';

const mode = window.__CFG__?.mode ?? 'origin';

const impl = mode === 'edge' ? edge : origin;

export const getProtooUrl = impl.getProtooUrl;
