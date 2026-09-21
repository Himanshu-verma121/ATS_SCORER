import spacy
from sentence_transformers import SentenceTransformer
from typing import Dict, List, Optional
from backend.models.schemas import IssueDetail
from backend.services.groq_parser import parse_resume, parse_job_description
from backend.services.jd_matcher import compare_resume_with_jd
from backend.services.feedback_engine import analyze_issues, generate_issues_summary
from backend.services.ats_scorer import calculate_overall_score, validate_skills_with_projects

def analyze_full_resume(
    resume_text: str,
    nlp: spacy.Language,
    embedder: SentenceTransformer,
    job_description: Optional[str] = None,
) -> Dict:
    import logging
    logger = logging.getLogger('ats_resume_scorer')

    print("ANALYZER: started", flush=True)

    print("ANALYZER: starting Groq resume parsing", flush=True)
    parsed_resume = parse_resume(resume_text)
    print("ANALYZER: Groq resume parsing completed", flush=True)

    logger.info(f"Groq parsed summary: {parsed_resume.get('professional_summary', '')[:100]!r}")
    logger.info(f"Groq parsed skills count: {len(parsed_resume.get('skills', []))}")
    logger.info(f"Groq parsed experience count: {len(parsed_resume.get('experience', []))}")

    skills          = parsed_resume.get('skills', [])
    projects        = parsed_resume.get('projects', [])
    keywords        = parsed_resume.get('keywords', [])
    action_verbs    = parsed_resume.get('action_verbs', [])

    experience_months = sum(
        int(e.get('duration_months', 0))
        for e in parsed_resume.get('experience', [])
        if isinstance(e, dict)
    )

    contact_info = {
        'email':     parsed_resume.get('email'),
        'phone':     parsed_resume.get('phone'),
        'linkedin':  parsed_resume.get('linkedin'),
        'github':    parsed_resume.get('github'),
        'portfolio': None,
    }

    print("ANALYZER: starting skill validation", flush=True)

    skill_validation = validate_skills_with_projects(
        skills=skills,
        projects=projects,
        experience_entries=parsed_resume.get('experience', []),
        embedder=embedder,
    )

    print("ANALYZER: skill validation completed", flush=True)

    jd_comparison_result = None
    jd_keywords = None

    if job_description and job_description.strip():

        print("ANALYZER: starting JD parsing", flush=True)

        parsed_jd = parse_job_description(job_description.strip())

        print("ANALYZER: JD parsing completed", flush=True)

        jd_keywords = list(set(
            parsed_jd.get('keywords', []) +
            parsed_jd.get('required_skills', []) +
            parsed_jd.get('preferred_skills', [])
        ))

        print("ANALYZER: starting JD comparison", flush=True)

        jd_comparison_result = compare_resume_with_jd(
            resume_text=resume_text,
            resume_keywords=keywords,
            resume_skills=skills,
            jd_text=job_description.strip(),
            jd_keywords=jd_keywords,
            embedder=embedder,
            nlp=nlp,
        )

        print("ANALYZER: JD comparison completed", flush=True)

    from backend.utils.file_utils import (
        get_default_grammar_results, get_default_location_results,
    )

    grammar_results  = get_default_grammar_results()
    location_results = get_default_location_results()

    print("ANALYZER: starting ATS score calculation", flush=True)

    scores = calculate_overall_score(
        text=resume_text,
        parsed_resume=parsed_resume,
        skills=skills,
        keywords=keywords,
        action_verbs=action_verbs,
        skill_validation_results=skill_validation,
        grammar_results=grammar_results,
        location_results=location_results,
        jd_keywords=jd_keywords,
        experience_months=experience_months,
    )

    print("ANALYZER: ATS score calculation completed", flush=True)

    print("ANALYZER: starting feedback analysis", flush=True)

    detailed_feedback = analyze_issues(
        resume_text=resume_text,
        parsed_resume=parsed_resume,
        skills=skills,
        projects=projects,
        action_verbs=action_verbs,
        skill_validation=skill_validation,
        scores=scores,
        contact_info=contact_info,
    )

    print("ANALYZER: feedback analysis completed", flush=True)

    print("ANALYZER: generating issues summary", flush=True)

    issues_summary = generate_issues_summary(detailed_feedback)

    print("ANALYZER: issues summary completed", flush=True)

    validated_raw   = skill_validation.get('validated_skills', [])
    unvalidated_raw = skill_validation.get('unvalidated_skills', [])
    total_skills    = len(validated_raw) + len(unvalidated_raw)

    val_pct = round(
        (len(validated_raw) / total_skills * 100)
        if total_skills > 0 else 0,
        1
    )

    skill_validation_details = {
        "validated": [
            {
                "skill":    item['skill'],
                "projects": item.get('projects', []),
            }
            for item in validated_raw
        ],
        "unvalidated":     unvalidated_raw,
        "total":           total_skills,
        "validated_count": len(validated_raw),
        "validation_pct":  val_pct,
    }

    print("ANALYZER: building final result", flush=True)

    return {
        "ATS_score":          scores['overall_score'],
        "ats_score":          scores['overall_score'],
        "component_scores": {
            "formatting":       scores['formatting_score'],
            "keywords":         scores['keywords_score'],
            "content":          scores['content_score'],
            "skill_validation": scores['skill_validation_score'],
            "ats_compatibility": scores['ats_compatibility_score'],
        },
        "issues_summary":    issues_summary,
        "detailed_feedback": detailed_feedback,
        "jd_match_analysis": jd_comparison_result,
        "jd_comparison":     jd_comparison_result,
        "skills":            skills,
        "matched_keywords":  (
            jd_comparison_result['matched_keywords']
            if jd_comparison_result else list(keywords[:20])
        ),
        "missing_keywords":  (
            jd_comparison_result['missing_keywords']
            if jd_comparison_result else []
        ),
        "strengths": _generate_strengths(
            parsed_resume,
            skills,
            projects,
            action_verbs,
            skill_validation,
            scores
        ),
        "interpretation":    scores.get('overall_interpretation', ''),
        "skill_validation_details": skill_validation_details,
        "experience_months": experience_months,
    }

def _generate_strengths(
    parsed_resume: Dict, skills: List, projects: List,
    action_verbs: List, skill_validation: Dict, scores: Dict,
) -> List[str]:
    """Generate a list of things the resume does well, based on actual structured data."""
    strengths = []

    if parsed_resume.get('experience'):
        strengths.append("Has a dedicated Experience section")
    if parsed_resume.get('projects') or len(projects) > 0:
        strengths.append("Includes a Projects section showcasing applied skills")
    if parsed_resume.get('education'):
        strengths.append("Education section is present")
    if parsed_resume.get('skills'):
        strengths.append("Clear Skills section with listed technologies")
    if parsed_resume.get('professional_summary', '').strip():
        strengths.append("Professional Summary provides a quick overview")

    if len(skills) >= 8:
        strengths.append(f"Strong skill set — {len(skills)} skills detected")
    if len(action_verbs) >= 5:
        strengths.append(f"Uses {len(action_verbs)} strong action verbs in bullet points")

    validated = skill_validation.get('validated_skills', [])
    if len(validated) >= 3:
        strengths.append(f"{len(validated)} skills are backed by project/experience evidence")

    if scores.get('formatting_score', 0) >= 16:
        strengths.append("Well-formatted and ATS-friendly structure")
    if scores.get('content_score', 0) >= 20:
        strengths.append("Content quality is high with measurable achievements")

    return strengths