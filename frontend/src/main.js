import './style.css';
import { SurveyEngine } from './survey.js';

const app = document.getElementById('app');
window._engine = new SurveyEngine(app);
